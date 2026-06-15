# LLM Shadow Proxy

## What is this and why does it exist?

Imagine your product uses a GPT-4 model to make decisions — like approving a loan, cancelling an order, or routing a support ticket. Your team wants to switch to a newer, cheaper model. But how do you know the new model gives the same answers? You can't just swap it in and hope for the best in production.

**Shadow testing** solves this. Every real user request goes to your current (primary) model as usual — the user gets their response with zero added delay. At the same time, the same request is secretly sent to the new (candidate) model in the background. The two responses are compared. If they disagree, that's a **mismatch** — logged and stored for your team to review.

This service is the infrastructure that makes that happen at scale (millions of requests/day).

---

## High-Level Design (HLD)

The proxy publishes the shadow event to the queue **before** it awaits the
primary LLM, so the candidate call runs *in parallel* with primary instead of
after it. Both sides write their result into a shared `shadow_tasks` checkpoint
row and then race to run the comparison — an atomic claim guarantees it happens
exactly once, on whichever side finishes last.

```mermaid
graph TD
    Client(["fa:fa-user Client"])
    Proxy["Proxy Service (publish side)<br/>POST /v1/chat · GET /metrics · PUT /config"]
    PrimaryLLM(["Primary LLM<br/>(GPT-4 / current model)"])
    Queue[["SNS topic to SQS queue<br/>(in-memory in dev)"]]
    Worker["Shadow Worker Service<br/>(consume side · separate container)"]
    CandidateLLM(["Candidate LLM<br/>(new model under test)"])
    Checkpoint[("shadow_tasks<br/>checkpoint + atomic claim<br/>PostgreSQL / SQLite")]
    Compare["runComparison()<br/>Domain Evaluator<br/>GENERIC / JOB_CANCEL / ORDER_CANCEL"]
    DB[("Mismatch DB<br/>PostgreSQL / SQLite")]
    Cache[("Cache (Redis / in-memory)<br/>Metrics + Config")]
    Storage[("Blob Storage (S3 / local)<br/>primary.json + candidate.json")]

    Client -->|"Step 1: POST /v1/chat {messages}"| Proxy
    Proxy -.->|"Step 2: publish trigger FIRST<br/>{taskId, taskType, messages}"| Queue
    Proxy -->|"Step 3: await primary chat() (blocking)"| PrimaryLLM
    PrimaryLLM -->|"Step 4: primary response"| Proxy
    Proxy -->|"Step 5: return response to user"| Client

    Queue -->|"Step 6: dequeue (runs parallel to steps 3-5)"| Worker
    Worker -->|"Step 7: call candidate chat()"| CandidateLLM
    CandidateLLM -->|"Step 8: candidate response"| Worker

    Proxy -.->|"Step 9a: put primary.json"| Storage
    Worker -.->|"Step 9b: put candidate.json"| Storage
    Proxy -.->|"Step 10a: upsertPrimaryDone + tryClaimComparison"| Checkpoint
    Worker -.->|"Step 10b: upsertCandidateDone + tryClaimComparison"| Checkpoint

    Checkpoint -->|"Step 11: winner of atomic claim runs comparison"| Compare
    Compare -->|"Step 12: save mismatch (only if differ)"| DB
    Compare -->|"Step 13: markComparisonDone"| Checkpoint
    Compare -->|"Step 14: recordShadowResult (metrics)"| Cache

    style Client fill:#4a90d9,color:#fff
    style PrimaryLLM fill:#7b68ee,color:#fff
    style CandidateLLM fill:#7b68ee,color:#fff
    style Queue fill:#f0ad4e,color:#000
    style Worker fill:#5cb85c,color:#fff
    style Proxy fill:#5bc0de,color:#000
    style Compare fill:#5cb85c,color:#fff
    style Checkpoint fill:#d9534f,color:#fff
    style DB fill:#d9534f,color:#fff
    style Cache fill:#d9534f,color:#fff
    style Storage fill:#d9534f,color:#fff
```

> **Solid arrows** = blocking (user waits). **Dashed arrows** = fire-and-forget (user doesn't wait).
>
> **Step ordering that makes this fast:** Step 2 (publish) fires *before* Step 3 (await primary), so the worker's candidate call (Steps 6–8) overlaps with the primary call (Steps 3–5). Steps 9–10 run on **both** sides independently; whichever finishes last wins the atomic claim at Step 11 and runs the comparison exactly once.
>
> **Does Step 2 before Step 3 deprioritize the user? No.** Step 2 is `safeTask(queue.publish(...))`, which calls `asyncio.create_task` and returns in microseconds — it does **not** `await` the publish. Because `create_task` doesn't run the coroutine body synchronously, the SNS publish only starts executing at the next `await` (the primary call on the very next line) and then runs *concurrently* with it on the event loop. So the only thing on the user's critical path is the primary LLM call; the publish overlaps it and, if still in flight when primary returns, the user is already gone. Publishing first is what lets the candidate call overlap primary — `await`-ing the publish here (instead of fire-and-forget) is the anti-pattern that *would* make every user pay SNS latency, and the code deliberately avoids it.

---

## Components — what each part does and why

Every component sits behind a **port** (interface), so the same code runs with
in-memory/SQLite/local adapters for dev and tests, and with the managed-service
adapters listed below in production. Swap them with one line in `cloud.env`.

### Proxy Service *(publish side — `app/main.py`)*
- **What it does:** the only public HTTP service. Serves `POST /v1/chat`, calls the primary LLM, returns the response, and fires the shadow event. Also serves `GET /metrics` and `PUT /config`.
- **Why it's separate from the worker:** the request path must stay fast and predictable. Keeping the candidate LLM call out of this process means a slow/failing candidate can never affect user latency, and the two sides scale independently.

### Primary LLM *(the current production model)*
- **What it does:** the model your users actually talk to. Its response is the **only** thing on the user's critical path.
- **Why bounded:** wrapped in a circuit breaker + `asyncio.wait_for` timeout so a misbehaving upstream fails fast (503/504) instead of hanging requests.

### Candidate LLM *(the new model under test)*
- **What it does:** the model you're evaluating. Called only by the worker, fully off the user path. Swappable (OpenAI → Groq → anything) by changing `CANDIDATE_LLM_*` — no code change, because it's behind the `LlmPort` interface.

### Message Queue — **SNS → SQS** *(`QUEUE_BACKEND`: `sqs` | `memory`)*
- **What it does:** decouples the proxy from the worker. The proxy publishes a lightweight trigger `{taskId, taskType, messages}` to an **SNS topic**, which fans out to one or more **SQS queues** the workers long-poll.
- **Why SNS + SQS (not a direct call):** durability + buffering + fan-out. If all workers are down, messages wait in SQS instead of being lost; bursts are absorbed by the queue; adding a new candidate model is just another SNS subscription. SQS gives at-least-once delivery and a retry/visibility-timeout story for free.
- **Why not put the primary response in the message:** that would force the worker to wait for primary before starting the candidate, killing the parallelism. The message is only a trigger.

### Shadow Worker Service *(consume side — `app/worker.py`)*
- **What it does:** a separate container (`python -m app.worker`) that long-polls SQS, calls the candidate LLM, archives the result, and participates in the comparison. Scale it horizontally (`--scale worker=N`) when evaluation is the bottleneck.
- **Safety valve:** a bounded `ShadowPool` load-sheds (drops + counts) when more shadow tasks arrive than `MAX_CONCURRENT_SHADOWS`, so a traffic spike can't exhaust memory.

### Blob Storage — **S3** *(`STORAGE_BACKEND`: `s3` | `local` | `azure`)*
- **What it stores:** the full raw responses — `primary.json` (written by the proxy) and `candidate.json` (written by the worker), keyed by `tasks/<taskType>/<YYYY/MM/DD>/<taskId>/`.
- **Why S3:** these blobs are large, write-once, and read rarely (offline replay, debugging, audits). Object storage is the cheapest durable home for that and scales infinitely — you would never want full LLM payloads bloating your relational DB. Each side writes its own object independently, so there's no coordination on the hot path.

### Checkpoint + Mismatch DB — **PostgreSQL** *(`DB_BACKEND`: `postgres` | `sqlite`)*

One relational database, used for two related jobs:

- **`shadow_tasks` (checkpoint + coordination):** one row per task tracking `is_primary_done`, `is_candidate_done`, `is_comparison_triggered`, plus S3 paths and parsed actions. This is the **synchronization point** between the two services. The atomic `UPDATE … WHERE is_comparison_triggered = 0` is what guarantees the comparison runs **exactly once** even though both sides race to it — that needs a real transactional store, which is why this is PostgreSQL and not Redis. It also enables crash recovery: a stalled row (`findStalled`) can be re-queued.
- **`mismatches` (queryable system of record):** permanent, structured records of every disagreement, with severity and diff fields. We need rich queries here ("all `ORDER_CANCELLATION` mismatches with `CRITICAL` severity last week"), which is exactly what SQL indexes give you. (The same DB also holds `model_fleet` config.)

### Cache — **Redis** *(`CACHE_BACKEND`: `redis` | `memory`)*
- **What it stores:** the high-frequency, low-durability state — live **metrics counters** (`totalRequests`, `shadowCompleted`, `exactMatches`, `shedCount`, …) and the **runtime config** (the shadow sampling percentage).
- **Why Redis and not Postgres:** these are touched on basically every request (atomic `INCR` on counters, a `GET` on the sampling percentage). Redis makes those sub-millisecond and **shared across all proxy replicas**, so metrics aggregate correctly and a `PUT /config` change is seen by every instance instantly with no restart. Putting per-request `INCR`s in Postgres would add DB round-trips to the hot path and create write contention. Losing a counter on a Redis blip is acceptable; losing a mismatch record is not — which is the dividing line between what lives in Redis vs Postgres.

### Domain Evaluator (`runComparison`)
- **What it does:** not a service, but the comparison logic the claim-winner runs in-process. Picks the evaluator for the task type (`GENERIC` / `JOB_CANCELLATION` / `ORDER_CANCELLATION`) and decides match vs mismatch + severity. Shared code so the proxy *or* the worker can run it depending on who wins the claim.

---

## Request Sequence Diagram

The candidate call (worker) runs **concurrently** with the primary call
(proxy). Both sides write to the `shadow_tasks` checkpoint and call
`tryClaimComparison()`; the side that finishes last gets `claimed=true` and
runs the comparison once.

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant Proxy as Proxy Service
    participant PrimaryLLM as Primary LLM
    participant Queue as SNS to SQS
    participant Worker as Shadow Worker
    participant CandidateLLM as Candidate LLM
    participant Chk as shadow_tasks DB
    participant Storage as Blob Storage
    participant DB as Mismatch DB
    participant Cache as Redis Cache

    Client->>Proxy: POST /v1/chat {messages}
    Note over Proxy: getShadowPercentage() — Redis read ~0.5ms

    Proxy-)Queue: publish {taskId, taskType, messages}  (BEFORE awaiting primary)

    par primary path (blocking, user waits)
        Proxy->>PrimaryLLM: call primary model
        PrimaryLLM-->>Proxy: primary response
        Proxy-->>Client: 🟢 return response immediately
        Note over Client,Proxy: Client is DONE. Everything below is background.
        Proxy-)Cache: increment totalRequests
        Proxy-)Storage: archive primary.json
        Proxy->>Chk: upsertPrimaryDone(taskId)
        Proxy->>Chk: tryClaimComparison(taskId)
    and candidate path (parallel, in the worker)
        Queue-)Worker: dequeue message
        Note over Worker: ShadowPool.submit() — under MAX_CONCURRENT_SHADOWS cap?
        alt pool has capacity
            Worker->>CandidateLLM: call candidate model (timeout-bounded)
            CandidateLLM-->>Worker: candidate response
            Worker-)Storage: archive candidate.json
            Worker->>Chk: upsertCandidateDone(taskId)
            Worker->>Chk: tryClaimComparison(taskId)
        else pool full — traffic spike protection
            Worker->>Cache: recordShed()
            Note over Worker: coroutine dropped, no memory leak
        end
    end

    Note over Chk: exactly ONE side wins the atomic claim (both done + not yet claimed)
    Chk-->>Proxy: claimed=true (if proxy finished last)
    Chk-->>Worker: claimed=true (if worker finished last)

    Note over Proxy,Worker: the winner runs runComparison(task) — order below matches the code
    opt responses differ
        Proxy->>DB: save MismatchRecord
    end
    Proxy->>Chk: markComparisonDone(taskId, match|mismatch)
    Proxy->>Cache: recordShadowResult(exactMatch=true|false)
```

---

## Flow Diagram: what happens to every request

```mermaid
flowchart TD
    A(["Request arrives"]) --> SC{"Shadow<br/>sampling check"}
    SC -->|"sampled"| PUB["Step 1: publish trigger<br/>{taskId, taskType, messages}<br/>fire-and-forget"]
    SC -->|"not sampled"| B0["Call PRIMARY LLM"]
    PUB --> B["Step 2: call PRIMARY LLM"]

    B --> C["Step 3: return response to client<br/>(+ incrementRequests)"]
    B0 --> C0["Return response + incrementRequests<br/>+ archive primary.json (no compare)"]
    C0 --> Z2(["Done (no shadow)"])

    C --> AP["Step 4a: archive primary.json<br/>upsertPrimaryDone"]
    AP --> CLP["Step 5a: tryClaimComparison"]

    PUB -. "runs in parallel" .-> I["Step 2': worker dequeues message"]
    I --> J{"ShadowPool<br/>capacity?"}
    J -->|"pool full"| K["Drop + increment shedCount"]
    J -->|"capacity available"| L["Step 3': call CANDIDATE LLM"]
    L --> M{"Candidate<br/>returned OK?"}
    M -->|"timeout or error"| N["increment shadowErrors"]
    M -->|"success"| AC["Step 4b: archive candidate.json<br/>upsertCandidateDone"]
    AC --> CLC["Step 5b: tryClaimComparison"]

    CLP --> WON{"Won the<br/>atomic claim?"}
    CLC --> WON
    WON -->|"no (other side will run it)"| Z(["Done"])
    WON -->|"yes (both sides done)"| O["Step 6: runComparison<br/>domain evaluator"]

    O --> P{"Responses<br/>match?"}
    P -->|"exact match"| Q["increment exactMatches"]
    P -->|"mismatch"| R["Save MismatchRecord to DB"]
    R --> S["markComparisonDone<br/>increment shadowCompleted"]
    Q --> S

    style C fill:#2d8a4e,color:#fff
    style Z2 fill:#2d8a4e,color:#fff
    style K fill:#c0392b,color:#fff
    style N fill:#c0392b,color:#fff
    style R fill:#e67e22,color:#fff
    style Q fill:#2d8a4e,color:#fff
```

---

```
User sends a request
        │
        ▼
┌────────────────────────┐
│   Shadow Proxy         │
│                        │      ┌──────────────────────────────────────────┐
│ 1. Publish to SQS  ────┼─────►│  Shadow Worker (separate service)        │
│    {taskId, messages}  │      │                                          │
│       (BEFORE primary) │      │ 2. Ask CANDIDATE model   ◄── runs in     │
│                        │      │    (new LLM)                 PARALLEL     │
│ 2. Ask PRIMARY model ──┼──►   │ 3. Archive candidate.json    with primary│
│    (GPT-4)             │ User │ 4. upsertCandidateDone                   │
│       returns to user  │ done │ 5. tryClaimComparison ──┐                │
│ 3. Archive primary.json│      └─────────────────────────┼────────────────┘
│ 4. upsertPrimaryDone   │                                │
│ 5. tryClaimComparison ─┼────────────────┐               │
└────────────────────────┘                ▼               ▼
                               ┌──────────────────────────────────────┐
                               │  shadow_tasks (atomic claim)          │
                               │  exactly ONE side wins → compare:     │
                               │    ✓ Same → record match              │
                               │    ✗ Diff → save MismatchRecord       │
                               └──────────────────────────────────────┘
```

---

## The critical design rule: respond to the user first, do everything else later

The proxy does **one** blocking thing: call the primary model and return the answer to the user. Everything else — publishing to the queue, saving to storage, the checkpoint write, incrementing counters — is scheduled as a background task after the response is already on its way.

This is non-negotiable at scale. If SQS has a 100ms hiccup and we waited for it before responding, every user at that moment would feel that 100ms. With background tasks, users feel nothing.

There's one subtlety: the publish is fired **before** awaiting the primary
call. Publishing is itself fire-and-forget (`safeTask`), so it doesn't block —
but firing it first means the worker can start the candidate LLM call while the
primary call is still in flight, cutting end-to-end shadow latency.

```python
# What the code actually does (app/resources/proxy/proxyService.py):

# Fire the shadow event FIRST — fire-and-forget, candidate starts in parallel
if doShadow:
    safeTask(queue.publish(TOPIC, {taskId, taskType, messages}))

primaryResponse = await primaryLlm.chat(...)   # user waits ONLY for this

# After returning, a background task archives + checkpoints + maybe compares
safeTask(metrics.incrementRequests())                      # fire and forget
safeTask(archivePrimaryAndMaybeCompare(taskId, ...))       # fire and forget

return primaryResponse                          # user already getting this
```

`archivePrimaryAndMaybeCompare` writes `primary.json` to blob storage, calls
`upsertPrimaryDone`, then `tryClaimComparison` — and runs the comparison only if
it wins the claim (i.e. the worker already finished the candidate side).

---

## How the two services talk to each other

They don't — directly. Two things connect them, and **neither carries the
primary response**:

1. The **queue** (SNS→SQS in production, in-memory in dev/tests) — the proxy
   publishes a lightweight trigger so the worker can start the candidate call.
2. The **`shadow_tasks` table** — a shared checkpoint where each side records
   its result and atomically claims the comparison.

```
Proxy Service                         Shadow Worker Service
─────────────                         ─────────────────────
Publishes a TRIGGER (no response):    Picks up that message:
{                                       calls candidate LLM,
  taskId: "abc-123",                    archives candidate.json,
  taskType: "GENERIC",                  upsertCandidateDone(),
  messages: [...]                       tryClaimComparison()
}

        both sides converge on shadow_tasks
        ──────────────────────────────────
        is_primary_done · is_candidate_done · is_comparison_triggered
        → whoever finishes last wins the claim and runs the comparison
```

Why not put `primaryResponse` in the queue message? Because that would force
the worker to wait for primary to finish before the candidate call could start.
Publishing only the trigger lets the two LLM calls overlap.

This means:
- The proxy doesn't know or care what the worker does
- The worker can be on a completely different server (it is — a separate container)
- If the worker is down, users are unaffected — messages queue up and get processed when it comes back; stalled `shadow_tasks` rows can be recovered by a re-queue job (`findStalled`)
- You can run 1 proxy and 10 workers if evaluations are the bottleneck (`--scale worker=10`)

---

## Project structure

```
app/
├── main.py                 # FastAPI app (publish side); runs inline worker only when QUEUE_BACKEND=memory
├── worker.py               # standalone shadow worker entrypoint (python -m app.worker) for QUEUE_BACKEND=sqs
│
├── core/
│   ├── llmClient.py        # shared function to call any LLM endpoint
│   ├── shadowWorker.py     # consume side: dequeues, calls candidate, archives, claims comparison
│   └── shadowPool.py       # limits how many evaluations run at once (safety valve)
│
├── domain/
│   ├── comparison.py       # runComparison(): shared by proxy + worker, runs on the atomic-claim winner
│   ├── models/             # data shapes: TaskRequest, TaskResponse, ActionResult, etc.
│   └── evaluators/         # comparison logic per task type (generic, job, order)
│
├── ports/                  # interfaces (abstract classes) for external systems
│   ├── messageQueue.py     # what a queue must be able to do
│   ├── cache.py            # what a cache must be able to do
│   ├── database.py         # what a database must be able to do (mismatches, model fleet)
│   ├── shadowTask.py       # ShadowTask + ShadowTaskRepository (checkpoint + atomic claim)
│   └── blobStorage.py      # what blob storage must be able to do
│
├── adapters/               # concrete implementations of those interfaces
│   ├── queue/
│   │   ├── memory.py       # in-process queue (dev/tests — no AWS needed)
│   │   └── sqs.py          # AWS SQS (production)
│   ├── cache/
│   │   ├── memory.py       # Python dict (dev/tests)
│   │   └── redis.py        # Redis (production)
│   ├── database/
│   │   ├── sqlite.py       # SQLite file (dev/tests)
│   │   └── postgres.py     # PostgreSQL (production)
│   └── storage/
│       ├── local.py        # local filesystem (dev/tests)
│       ├── s3.py           # AWS S3 (production)
│       └── azureBlob.py    # Azure Blob Storage
│
├── infrastructure/
│   ├── container.py        # reads env vars, wires up the right adapters
│   └── dependencies.py     # FastAPI dependency injection helpers
│
└── resources/
    ├── proxy/              # POST /v1/chat — the main endpoint
    ├── metrics/            # GET /metrics
    └── config/             # PUT /config
```

### Why ports and adapters? (Hexagonal Architecture)

This pattern solves a real problem: your tests shouldn't need a real Redis/SQS/PostgreSQL running to pass. And switching from SQLite to PostgreSQL in production shouldn't require changing business logic.

A **port** is just a Python abstract class that says "anything that acts as a cache must have these methods: `get`, `set`, `hincrby`...". An **adapter** is the actual implementation for one specific technology.

```
Your code talks to the port (abstract)
         │
         ▼
    CachePort                   ← your business logic only knows this
    ├── InMemoryCacheAdapter    ← used in tests and local dev
    └── RedisAdapter            ← used in production
```

Swap the backend with one line in `cloud.env`. Zero code changes.

---

## What gets stored where

| Data | Where | Why |
|------|-------|-----|
| Request/response counters | Redis (or in-memory) | Needs to be fast (every request increments) and shared across multiple proxy instances |
| Shadow task checkpoint (`shadow_tasks`) | PostgreSQL (or SQLite) | Per-task pipeline state — `is_primary_done`, `is_candidate_done`, `is_comparison_triggered` — shared by proxy + worker for the atomic comparison claim and crash recovery |
| Mismatch records | PostgreSQL (or SQLite) | Permanent storage; needs to be queryable (show me all ORDER_CANCELLATION mismatches with CRITICAL severity) |
| Raw responses (`primary.json`, `candidate.json`) | S3 (or local file) | Large blobs; cheap storage; written independently by each side; used for offline replay and debugging |
| Shadow percentage config | Redis (or in-memory) | Needs to update live without restart; read on every request |

---

## Evaluators — how we decide if answers match

Different kinds of LLM tasks need different comparison logic. A generic "is the action field the same?" check isn't good enough for a cancellation decision where a $500 refund amount is involved.

| Task Type | What gets compared | When it's CRITICAL |
|---|---|---|
| `GENERIC` | `action` field exact match | — |
| `JOB_CANCELLATION` | decision, affected resources (>80% overlap required), rollback plan present | Primary says cancel-immediate, candidate says defer or reject |
| `ORDER_CANCELLATION` | decision, refund amount (±$0.01 tolerance), reason code, restock flag | Primary says full-refund, candidate says reject |

Add your own evaluator:
```python
EvaluatorRegistry.register(TaskType.MY_TASK, MyEvaluator())
```

---

## Safety valve — what happens when traffic spikes

`ShadowPool` (`app/core/shadowPool.py`) limits how many evaluations run at the same time (default: 50). If 200 shadow events arrive in a burst:

```
50  get processed normally
150 get dropped silently   ← shedCount in /metrics goes up by 150
```

Dropped evaluations never reach the candidate LLM, so a traffic spike can't cause an OOM crash or cascade failure. The `shedCount` metric tells you when you're hitting the cap so you can tune `MAX_CONCURRENT_SHADOWS` in `cloud.env`.

---

## API endpoints

### `POST /v1/chat` — the main proxy endpoint

Send the same payload you'd send directly to an OpenAI-compatible LLM.

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "system", "content": "You are a trading assistant. Reply with JSON: {\"action\": \"buy|sell|hold\"}"},
      {"role": "user", "content": "Tesla just beat earnings. What should I do?"}
    ]
  }'
```

You get back the primary model's response immediately. The shadow evaluation happens in the background — you never wait for it.

### `GET /metrics` — see how the two models compare

```bash
curl http://localhost:8000/metrics
```

```json
{
  "data": {
    "totalRequests": 1000,
    "shadowCompleted": 412,
    "exactMatchRatePct": 94.17,
    "shadowErrors": 3,
    "shedCount": 0
  }
}
```

| Field | Meaning |
|-------|---------|
| `totalRequests` | Total calls to `/v1/chat` |
| `shadowCompleted` | Evaluations that finished (primary vs candidate compared) |
| `exactMatchRatePct` | % of comparisons where both models gave the same answer |
| `shadowErrors` | Candidate LLM timed out or returned unparseable output |
| `shedCount` | Evaluations dropped because the pool was full |

### `PUT /config` — change shadow percentage live (no restart)

```bash
# Shadow 100% of traffic (every request gets evaluated)
curl -X PUT http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"shadowPercentage": 100}'

# Shadow only 10% (low-cost sampling in production)
curl -X PUT http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"shadowPercentage": 10}'

# Turn off all shadowing
curl -X PUT http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"shadowPercentage": 0}'
```

---

## Quick start

### Option 1: Local dev — no Docker, no AWS (fastest)

Everything runs in-memory. No Redis, no PostgreSQL, no SQS needed.

```bash
git clone <repository-url>
cd digitalOcean

python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Open cloud.env and fill in your LLM API keys:
#   PRIMARY_LLM_API_KEY=your-key-here
#   CANDIDATE_LLM_API_KEY=your-key-here
nano cloud.env

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Visit `http://localhost:8000/docs` for the interactive API explorer.

### Option 2: Docker — full distributed production stack

Spins up the complete distributed system: the proxy and the shadow worker run
as **separate** containers, talking to each other only through SNS + SQS — the
same topology you would run in production. Backends are all real services
(PostgreSQL, Redis, S3/SQS/SNS via LocalStack), so nothing is in-process.

```bash
# Fill in your LLM API keys first
nano cloud.env

docker compose -f dockerCompose.yml up --build
```

> The compose file is `dockerCompose.yml` (not the default name), so every
> command needs `-f dockerCompose.yml`.

```
Redis ──────────────────────────────────────────────────────► healthy
PostgreSQL ─────────────────────────────────────────────────► healthy
LocalStack (creates SNS topic, SQS queue, S3 bucket) ───────► healthy
                                                               │
                            ┌──────────────────────────────────┤
                            ▼                                   ▼
                    app  (publish side)              worker (consume side)
                POST /v1/chat → SNS topic       long-polls SQS → candidate LLM
                primary LLM + S3 archive        S3 archive + shadow_tasks + compare
```

The proxy never calls the candidate LLM itself — it publishes one SNS message
and returns. The separate worker consumes from SQS and runs the candidate +
comparison. Scale the consume side independently:

```bash
docker compose -f dockerCompose.yml up --build --scale worker=3
```

Startup order is enforced with health checks — `app` and `worker` will not
start until Redis, PostgreSQL, and LocalStack all report healthy.

---

## Configuration reference (`cloud.env`)

```env
# ── Which LLM to use ─────────────────────────────────────────────────
# PRIMARY: the model your users actually talk to
# CANDIDATE: the new model you want to test
PRIMARY_LLM_BASE_URL=https://inference.do-ai.run/v1
PRIMARY_LLM_API_KEY=your-api-key-here
PRIMARY_LLM_MODEL=openai-gpt-oss-120b

CANDIDATE_LLM_BASE_URL=https://inference.do-ai.run/v1
CANDIDATE_LLM_API_KEY=your-api-key-here
CANDIDATE_LLM_MODEL=openai-gpt-oss-120b

# ── How many concurrent shadow evaluations to allow ──────────────────
# If more than this arrive at once, extras are dropped (shedCount goes up)
SHADOW_TIMEOUT_SECONDS=30
MAX_CONCURRENT_SHADOWS=50

# ── Backend selection ─────────────────────────────────────────────────
# Change these to switch from dev to production backends.
# No code changes needed — just change the value and restart.
QUEUE_BACKEND=memory        # memory (dev/tests) | sqs (production)
CACHE_BACKEND=memory        # memory (dev/tests) | redis (production)
DB_BACKEND=sqlite           # sqlite (dev/tests) | postgres (production)
STORAGE_BACKEND=local       # local (dev/tests)  | s3 | azure

# ── SQLite — only used when DB_BACKEND=sqlite ─────────────────────────
MISMATCH_DB_PATH=mismatches.db

# ── Local file storage — only used when STORAGE_BACKEND=local ─────────
LOCAL_STORAGE_DIR=.shadow_storage

# ── AWS — only needed when QUEUE_BACKEND=sqs or STORAGE_BACKEND=s3 ────
# Leave AWS_ENDPOINT_URL blank for real AWS.
# Set to http://localhost:4566 when using LocalStack locally.
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_ENDPOINT_URL=
SNS_SHADOW_TOPIC_ARN=        # publish side (app): SNS topic to fan out shadow events
SQS_QUEUE_URL=               # consume side (worker): SQS queue URL to long-poll
S3_BUCKET=
S3_PREFIX=shadow-events

# ── Redis — only needed when CACHE_BACKEND=redis ──────────────────────
REDIS_URL=redis://localhost:6379

# ── PostgreSQL — only needed when DB_BACKEND=postgres ─────────────────
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# ── Azure Blob Storage — only needed when STORAGE_BACKEND=azure ───────
AZURE_STORAGE_CONNECTION_STRING=
AZURE_CONTAINER_NAME=
```

---

## Docker Compose services

| Service | Image | What it does |
|---------|-------|--------------|
| `app` | local build | The proxy (publish side): serves `/v1/chat`, calls the primary LLM, archives to S3, publishes the shadow event to SNS |
| `worker` | local build | The shadow worker (consume side): runs `python -m app.worker`, long-polls SQS, calls the candidate LLM, archives to S3, and runs comparison. Scale with `--scale worker=N` |
| `redis` | `redis:7-alpine` | Shared counters and live config. Used when `CACHE_BACKEND=redis` |
| `postgres` | `postgres:16-alpine` | Mismatch records + `shadow_tasks` checkpoint table. Used when `DB_BACKEND=postgres`. Credentials: user/pass/db all `shadow` |
| `localstack` | `localstack/localstack:3` | Runs SQS, SNS, S3 locally. `scripts/localstack-init.sh` creates the SNS topic `shadow-events`, the SQS queue `shadow-tasks` (subscribed to the topic), and the S3 bucket `shadow-events` at startup |

Both `app` and `worker` are built from the same image and share backend wiring
via a YAML anchor in `dockerCompose.yml`; only their command differs
(`uvicorn` vs `python -m app.worker`).

---

## Verifying the distributed stack step by step

After `docker compose -f dockerCompose.yml up --build -d`, walk through each
hop of the pipeline to confirm the whole distributed system works. (`COMPOSE`
is just shorthand below.)

```bash
COMPOSE="docker compose -f dockerCompose.yml"
```

**1. Every service is healthy**

```bash
$COMPOSE ps
# app, worker → Up;  redis, postgres, localstack → Up (healthy)
```

**2. LocalStack created the AWS resources**

```bash
$COMPOSE exec localstack awslocal sns list-topics       # → shadow-events
$COMPOSE exec localstack awslocal sqs list-queues        # → shadow-tasks
$COMPOSE exec localstack awslocal s3 ls                  # → shadow-events bucket
```

**3. The worker connected to SQS** (it logs the queue URL it is consuming)

```bash
$COMPOSE logs worker | grep "Shadow worker started"
# → consuming http://localstack:4566/000000000000/shadow-tasks
```

**4. The app is up and shadowing is on**

```bash
curl -s localhost:8000/health                                    # → {"status":"ok"}
curl -s -X PUT localhost:8000/config \
  -H 'Content-Type: application/json' -d '{"shadowPercentage":100}'
```

**5. Send a request through the proxy** — the primary response returns immediately

```bash
curl -s -X POST localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Should I buy or sell?"}]}' \
  | python3 -m json.tool
```

**6. Watch the worker consume the SQS message and call the candidate LLM**

```bash
$COMPOSE logs worker | tail -5
# → HTTP Request: POST https://api.groq.com/... "HTTP/1.1 200 OK"
```

**7. Confirm the checkpoint row in PostgreSQL** — both sides done, comparison ran once

```bash
$COMPOSE exec postgres psql -U shadow -d shadow -c \
  "SELECT task_id, is_primary_done, is_candidate_done, is_comparison_done,
          primary_action, candidate_action, comparison_result
     FROM shadow_tasks;"
# → is_primary_done=t  is_candidate_done=t  is_comparison_done=t  comparison_result=match
```

**8. Confirm both responses were archived to S3** (independent primary + candidate writes)

```bash
$COMPOSE exec localstack awslocal s3 ls s3://shadow-events --recursive
# → tasks/GENERIC/YYYY/MM/DD/<taskId>/primary.json
#   tasks/GENERIC/YYYY/MM/DD/<taskId>/candidate.json
```

**9. Confirm aggregated metrics** (counters live in Redis)

```bash
curl -s localhost:8000/metrics | python3 -m json.tool
# → totalRequests:1  shadowCompleted:1  exactMatchRatePct:100.0
```

**10. Inspect recorded mismatches** (only rows where the models disagreed)

```bash
$COMPOSE exec postgres psql -U shadow -d shadow -c \
  "SELECT timestamp, primary_action, candidate_action, severity FROM mismatches;"
```

**Tear down** (the `-v` drops the Postgres/Redis/LocalStack volumes):

```bash
$COMPOSE down -v
```

---

## Running the tests

```bash
pip install -r requirements.txt

pytest tests/ -v --tb=short           # all 59 tests
pytest tests/unit/ -v                 # unit tests only (no HTTP calls)
pytest tests/integration/ -v          # integration tests only (uses TestClient)
```

**No API keys or running services needed.** All LLM HTTP calls are mocked. The test suite uses SQLite and in-memory adapters throughout.

### What each test file covers

| File | Tests | What it verifies |
|------|-------|-----------------|
| `unit/test_metricsService.py` | 10 | Counters increment correctly, match rate is calculated right |
| `unit/test_shadowPool.py` | 5 | Pool accepts up to capacity, drops beyond it, cleans up after crashes |
| `unit/test_configService.py` | 5 | Shadow percentage updates, boundary values (0 and 100 are valid, 101 is not) |
| `unit/test_extractAction.py` | 11 | LLM response parsing handles valid JSON, missing keys, invalid JSON, nested objects |
| `integration/test_proxyRoute.py` | 14 | Full request flow: primary response returned, shadow fires, match/mismatch recorded, load shedding works |
| `integration/test_metricsRoute.py` | 5 | `/metrics` endpoint returns correct counts |
| `integration/test_configRoute.py` | 11 | `/config` validates input, shadow gating actually works at 0% and 100% |

### How the mocks work (for the curious)

The tests patch `_callLlm` at the module level where it's imported:

```python
# In the test:
with patch("app.resources.proxy.proxyService._callLlm", return_value=PRIMARY_RESPONSE):
    ...

# In the shadow worker test — separate patch since it's a separate service:
with patch("app.core.shadowWorker._callLlm", return_value=CANDIDATE_RESPONSE):
    ...
```

This mirrors the real architecture: the proxy and the shadow worker each make their own independent LLM calls.

---

## End-to-end walkthrough

For the **full distributed stack** (app + separate worker + Postgres + Redis +
LocalStack) see [Verifying the distributed stack step by step](#verifying-the-distributed-stack-step-by-step).
The quick local version below (single process, in-memory queue, SQLite) is the
fastest way to see the pipeline end-to-end:

```bash
# 1. Start the service (QUEUE_BACKEND=memory runs the worker in-process)
uvicorn app.main:app --port 8000

# 2. Set shadow to 100% so every request gets evaluated
curl -s -X PUT http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"shadowPercentage": 100}'

# 3. Send a chat request
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Should I buy or sell?"}]}' \
  | python3 -m json.tool
# → Primary model response arrives immediately (candidate runs in parallel)

# 4. Wait a moment for the background evaluation to finish, then check metrics
sleep 2
curl -s http://localhost:8000/metrics | python3 -m json.tool
# → shadowCompleted: 1, exactMatchRatePct: 100.0 (if models agreed)

# 5. Inspect the checkpoint + mismatch records directly (SQLite)
python3 -c "
import sqlite3
conn = sqlite3.connect('mismatches.db')
print('shadow_tasks:')
for r in conn.execute('SELECT task_id, is_primary_done, is_candidate_done, is_comparison_done, comparison_result FROM shadow_tasks'):
    print(' ', r)
print('mismatches:')
for r in conn.execute('SELECT timestamp, primary_action, candidate_action, severity FROM mismatches'):
    print(f'  {r[0]}  primary={r[1]}  candidate={r[2]}  severity={r[3]}')
conn.close()
"
```

---

## CI

The GitHub Actions pipeline runs on every push:

1. Checkout code
2. Install Python 3.13 + dependencies
3. Run all 59 tests

No real API keys or external services needed in CI — all LLM calls are mocked.
