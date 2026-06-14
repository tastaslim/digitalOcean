# LLM Shadow Proxy

## What is this and why does it exist?

Imagine your product uses a GPT-4 model to make decisions — like approving a loan, cancelling an order, or routing a support ticket. Your team wants to switch to a newer, cheaper model. But how do you know the new model gives the same answers? You can't just swap it in and hope for the best in production.

**Shadow testing** solves this. Every real user request goes to your current (primary) model as usual — the user gets their response with zero added delay. At the same time, the same request is secretly sent to the new (candidate) model in the background. The two responses are compared. If they disagree, that's a **mismatch** — logged and stored for your team to review.

This service is the infrastructure that makes that happen at scale (millions of requests/day).

---

## High-Level Design (HLD)

```mermaid
graph TD
    Client(["👤 Client"])
    Proxy["Proxy Service\n(control plane)\nPOST /v1/chat\nGET /metrics\nPUT /config"]
    PrimaryLLM(["🧠 Primary LLM\n(GPT-4 / current model)"])
    Queue[["📬 Message Queue\n(SQS in prod\nin-memory in dev)"]]
    Worker["Shadow Worker Service\n(background consumer)"]
    CandidateLLM(["🧠 Candidate LLM\n(new model under test)"])
    Evaluator["Domain Evaluator\nGENERIC / JOB_CANCEL\nORDER_CANCEL"]
    DB[("🗄️ Mismatch DB\n(PostgreSQL / SQLite)")]
    Cache[("⚡ Cache\n(Redis / in-memory)\nMetrics + Config")]
    Storage[("🪣 Blob Storage\n(S3 / local)\nRaw archives")]

    Client -->|"POST /v1/chat"| Proxy
    Proxy -->|"1 — await (blocking)"| PrimaryLLM
    PrimaryLLM -->|"response"| Proxy
    Proxy -->|"2 — return immediately"| Client
    Proxy -.->|"3 — fire & forget\ncreate_task"| Queue
    Proxy -.->|"4 — fire & forget\ncreate_task"| Storage
    Proxy -.->|"5 — fire & forget\ncreate_task"| Cache
    Queue -->|"dequeue"| Worker
    Worker -->|"call candidate LLM"| CandidateLLM
    CandidateLLM -->|"response"| Worker
    Worker --> Evaluator
    Evaluator -->|"mismatch"| DB
    Evaluator -->|"metrics"| Cache

    style Client fill:#4a90d9,color:#fff
    style PrimaryLLM fill:#7b68ee,color:#fff
    style CandidateLLM fill:#7b68ee,color:#fff
    style Queue fill:#f0ad4e,color:#000
    style Worker fill:#5cb85c,color:#fff
    style Proxy fill:#5bc0de,color:#000
    style Evaluator fill:#5cb85c,color:#fff
    style DB fill:#d9534f,color:#fff
    style Cache fill:#d9534f,color:#fff
    style Storage fill:#d9534f,color:#fff
```

> Solid arrows = **blocking** (user waits). Dashed arrows = **fire-and-forget** (user doesn't wait).

---

## Request Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant Proxy as Proxy Service
    participant PrimaryLLM as Primary LLM
    participant Queue as SQS Queue
    participant Worker as Shadow Worker
    participant CandidateLLM as Candidate LLM
    participant Evaluator as Domain Evaluator
    participant DB as Mismatch DB
    participant Cache as Redis Cache

    Client->>Proxy: POST /v1/chat {messages}

    Note over Proxy: getShadowPercentage() — Redis read ~0.5ms

    Proxy->>PrimaryLLM: call primary model
    PrimaryLLM-->>Proxy: primary response

    Proxy-->>Client: 🟢 return response immediately
    Note over Client,Proxy: Client is DONE. Everything below is background.

    par fire-and-forget background tasks
        Proxy-)Queue: publish {taskId, messages, primaryResponse}
        Proxy-)Cache: increment totalRequests
        Proxy-)DB: archive raw event to blob storage
    end

    Queue-)Worker: dequeue message

    Note over Worker: ShadowPool.submit() — check if under MAX_CONCURRENT_SHADOWS cap
    alt pool has capacity
        Worker->>CandidateLLM: call candidate model (timeout-bounded)
        CandidateLLM-->>Worker: candidate response

        Worker->>Evaluator: evaluate(primaryResponse, candidateResponse)

        alt responses match
            Evaluator->>Cache: recordShadowResult(exactMatch=true)
        else responses differ
            Evaluator->>DB: save MismatchRecord
            Evaluator->>Cache: recordShadowResult(exactMatch=false)
        end
    else pool full — traffic spike protection
        Worker->>Cache: recordShed()
        Note over Worker: coroutine dropped, no memory leak
    end
```

---

## Flow Diagram: what happens to every request

```mermaid
flowchart TD
    A([Request arrives]) --> B[Call PRIMARY LLM]
    B --> C[Return response to client]
    C --> D{Shadow\nsampling\ncheck}

    D -->|random % < shadowPercentage| E[Publish to queue\nfire-and-forget]
    D -->|not sampled| F([Done — no shadow])

    E --> G[Archive to blob storage\nfire-and-forget]
    G --> H([Client already got\ntheir response])

    E --> I[Worker dequeues message]
    I --> J{ShadowPool\ncapacity?}

    J -->|pool full| K[Drop + increment shedCount]
    J -->|capacity available| L[Call CANDIDATE LLM]

    L --> M{Candidate\nreturned OK?}
    M -->|timeout or error| N[increment shadowErrors]
    M -->|success| O[Run domain evaluator]

    O --> P{Responses\nmatch?}
    P -->|exact match| Q[increment exactMatches]
    P -->|mismatch| R[Save MismatchRecord to DB]
    R --> S[increment shadowCompleted]
    Q --> S

    style C fill:#2d8a4e,color:#fff
    style H fill:#2d8a4e,color:#fff
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
┌───────────────────┐
│   Shadow Proxy    │
│                   │
│ 1. Ask PRIMARY    │──────────────────► User gets this response immediately
│    model (GPT-4)  │                    (user never waits for anything else)
│                   │
│ 2. In background: │
│    publish to SQS │
└───────────────────┘
          │
          │  (completely separate service — user is already done)
          ▼
┌───────────────────────┐
│   Shadow Worker       │
│                       │
│ 3. Ask CANDIDATE      │
│    model (new LLM)    │
│                       │
│ 4. Compare answers    │
│    ✓ Same → log match │
│    ✗ Diff → log alert │
└───────────────────────┘
```

---

## The critical design rule: respond to the user first, do everything else later

The proxy does **one** blocking thing: call the primary model and return the answer to the user. Everything else — publishing to the queue, saving to storage, incrementing counters — is scheduled as a background task after the response is already on its way.

This is non-negotiable at scale. If SQS has a 100ms hiccup and we waited for it before responding, every user at that moment would feel that 100ms. With background tasks, users feel nothing.

```python
# What the code actually does:
primaryResponse = await callPrimaryLLM(...)   # user waits ONLY for this

# These three lines do NOT block the user — they're background tasks
asyncio.create_task(publishToQueue(...))       # fire and forget
asyncio.create_task(archiveToS3(...))          # fire and forget
asyncio.create_task(metrics.increment())       # fire and forget

return primaryResponse                         # user already getting this
```

---

## How the two services talk to each other

They don't — directly. The **queue** (AWS SQS in production, in-memory in dev/tests) is the only connection.

```
Proxy Service                    Shadow Worker Service
─────────────                    ─────────────────────
Publishes a message:             Picks up that message:
{                                calls candidate LLM,
  taskId: "abc-123",             compares answers,
  messages: [...],               saves mismatches to DB,
  primaryResponse: {...}         updates metrics
}
```

This means:
- The proxy doesn't know or care what the worker does
- The worker can be on a completely different server
- If the worker is down, users are unaffected — messages queue up and get processed when it comes back
- You can run 1 proxy and 10 workers if evaluations are the bottleneck

---

## Project structure

```
app/
├── core/
│   ├── llmClient.py        # shared function to call any LLM endpoint
│   ├── shadowWorker.py     # background service: dequeues, calls candidate, evaluates
│   └── shadowPool.py       # limits how many evaluations run at once (safety valve)
│
├── domain/
│   ├── models/             # data shapes: TaskRequest, TaskResponse, ActionResult, etc.
│   └── evaluators/         # comparison logic per task type (generic, job, order)
│
├── ports/                  # interfaces (abstract classes) for external systems
│   ├── messageQueue.py     # what a queue must be able to do
│   ├── cache.py            # what a cache must be able to do
│   ├── database.py         # what a database must be able to do
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
| Mismatch records | PostgreSQL (or SQLite) | Permanent storage; needs to be queryable (show me all ORDER_CANCELLATION mismatches with CRITICAL severity) |
| Raw request archives | S3 (or local file) | Large blobs; cheap storage; used for offline replay and debugging |
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

### Option 2: Docker — all production-like services

Spins up the app + Redis + PostgreSQL + LocalStack (which emulates SQS and S3 locally).

```bash
# Fill in API keys first
nano cloud.env

docker compose up --build
```

Docker Compose starts services in the right order:

```
Redis ──────────────────────────────────────────────────────► healthy
PostgreSQL ─────────────────────────────────────────────────► healthy
LocalStack (creates SNS topic, SQS queue, S3 bucket) ───────► healthy
                                                               │
                                                               ▼
                                                           App starts
```

The startup order is enforced with health checks — the app will not start until all three dependencies report healthy.

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
SNS_SHADOW_TOPIC_ARN=
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
| `app` | local build | The shadow proxy |
| `redis` | `redis:7-alpine` | Shared counters and live config. Used when `CACHE_BACKEND=redis` |
| `postgres` | `postgres:16-alpine` | Mismatch records. Used when `DB_BACKEND=postgres`. Credentials: user/pass/db all `shadow` |
| `localstack` | `localstack/localstack:3` | Runs SQS, SNS, S3 locally on your machine. `scripts/localstack-init.sh` creates all the required resources at startup |

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

```bash
# 1. Start the service
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
# → Primary model response arrives immediately

# 4. Wait 2 seconds for the background evaluation to finish, then check metrics
sleep 2
curl -s http://localhost:8000/metrics | python3 -m json.tool
# → shadowCompleted: 1, exactMatchRatePct: 100.0 (if models agreed)

# 5. If using SQLite, inspect mismatch records directly
python3 -c "
import sqlite3
conn = sqlite3.connect('mismatches.db')
rows = conn.execute('SELECT timestamp, primary_action, candidate_action, severity FROM mismatches').fetchall()
for r in rows:
    print(f'{r[0]}  primary={r[1]}  candidate={r[2]}  severity={r[3]}')
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
