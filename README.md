# LLM Shadow Proxy

A production FastAPI service that routes customer traffic to a **primary** LLM while
asynchronously shadowing every request to a **candidate** LLM in the background.
Response quality is evaluated deterministically and exposed via a real-time metrics
endpoint. Shadow concurrency is strictly bounded to protect the primary request path
under load. Action-key mismatches are streamed to a local SQLite file for offline
debugging.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          FastAPI  —  API Layer                            │
│                                                                          │
│        POST /v1/chat          GET /metrics          PUT /config           │
└─────────────────┬────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────┐
│           proxyService           │
│                                 │
│  ① increment totalRequests      │
│  ② build primary payload        │
│  ③ await Primary LLM  ──────────┼──────────────────────► Response to
│  ④ sample shadowPercentage      │                         client (sync,
│  ⑤ submit to ShadowPool        │                         immediate)
└─────────────────┬───────────────┘
                  │  asyncio.create_task  (non-blocking, returns immediately)
                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Shadow Evaluation Pool                            │
│                                                                         │
│   Capacity gate  ──  _active >= MAX_CONCURRENT_SHADOWS?                 │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │  YES → close coroutine, recordShed()  (memory-safe load shed)  │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                  │ NO                                                    │
│                  ▼                                                       │
│   Await Candidate LLM  (bounded by SHADOW_TIMEOUT_SECONDS)              │
│                  │                                                       │
│                  ▼                                                       │
│   Extract `action` key from both responses (parsed JSON)                │
│                  │                                                       │
│       ┌──────────┴──────────────────────┐                               │
│       │ both parseable JSON?            │                               │
│       │                                 │                               │
│      NO                                YES                              │
│       │                    ┌────────────┴──────────┐                   │
│       ▼                    │  actions equal?        │                   │
│  recordShadowResult        │                        │                   │
│  (exactMatch=False)       YES                      NO                  │
│                             │                       │                   │
│                             ▼                       ▼                   │
│                    recordShadowResult      write to mismatches.db       │
│                    (exactMatch=True)       recordShadowResult           │
│                                           (exactMatch=False)            │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Endpoints

| Method | Path        | Description                                              |
|--------|-------------|----------------------------------------------------------|
| `POST` | `/v1/chat`  | Proxy chat request to primary LLM; shadow to candidate  |
| `GET`  | `/metrics`  | Real-time shadow evaluation counters                     |
| `PUT`  | `/config`   | Hot-update shadow routing percentage                     |

---

## Configuration (`cloud.env`)

```env
# Shared DO inference token (fallback when per-endpoint keys are blank)
API_KEY=doo_v1_...

PRIMARY_LLM_BASE_URL=https://inference.do-ai.run/v1
PRIMARY_LLM_API_KEY=          # leave blank to use API_KEY
PRIMARY_LLM_MODEL=openai-gpt-oss-120b

CANDIDATE_LLM_BASE_URL=https://inference.do-ai.run/v1
CANDIDATE_LLM_API_KEY=        # leave blank to use API_KEY
CANDIDATE_LLM_MODEL=openai-gpt-oss-120b

SHADOW_TIMEOUT_SECONDS=30     # max wait for candidate response
MAX_CONCURRENT_SHADOWS=50     # pool cap — excess requests are shed
```

---

## Setup

```bash
# 1. Clone and enter the repo
git clone <repository-url>
cd digitalOcean

# 2. Create virtual environment
python -m venv venv && source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure credentials
#    Edit cloud.env and set PRIMARY_LLM_API_KEY / CANDIDATE_LLM_API_KEY (or API_KEY)

# 5. Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or with Docker:

```bash
docker compose up --build
```

Interactive docs: http://localhost:8000/docs

---

## Step-by-step curl walkthrough — watching metrics evolve

### Step 1 — Confirm zero state

```bash
curl -s http://localhost:8000/metrics | python3 -m json.tool
```

```json
{
  "status": 200,
  "data": {
    "totalRequests": 0,
    "shadowErrors": 0,
    "shadowCompleted": 0,
    "exactMatchRatePct": 0.0,
    "shedCount": 0
  }
}
```

### Step 2 — Send a chat request (primary + shadow both fire at 100%)

```bash
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "system", "content": "Always reply with JSON containing an action key."},
      {"role": "user",   "content": "What should I do next?"}
    ],
    "max_tokens": 150
  }' | python3 -m json.tool
```

You receive the primary LLM response immediately. The shadow fires in the background.

### Step 3 — Check metrics (wait ~2 s for shadow to complete)

```bash
sleep 2 && curl -s http://localhost:8000/metrics | python3 -m json.tool
```

```json
{
  "data": {
    "totalRequests": 1,
    "shadowCompleted": 1,
    "exactMatchRatePct": 100.0,
    "shadowErrors": 0,
    "shedCount": 0
  }
}
```

### Step 4 — Throttle shadow traffic to 50%

```bash
curl -s -X PUT http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"shadowPercentage": 50}' | python3 -m json.tool
```

```json
{
  "status": 200,
  "data": {"shadowPercentage": 50.0},
  "message": "Config updated"
}
```

### Step 5 — Send several requests and observe partial shadowing

```bash
for i in {1..6}; do
  curl -s -X POST http://localhost:8000/v1/chat \
    -H "Content-Type: application/json" \
    -d '{"messages":[{"role":"user","content":"hello"}]}' > /dev/null
done
sleep 3 && curl -s http://localhost:8000/metrics | python3 -m json.tool
```

`shadowCompleted` will be roughly 3 out of 6 (50% sampling is probabilistic).

### Step 6 — Disable all shadowing

```bash
curl -s -X PUT http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"shadowPercentage": 0}' | python3 -m json.tool
```

Subsequent `/v1/chat` calls now return instantly with zero shadow overhead.

### Step 7 — Inspect SQLite mismatches (if any)

```bash
sqlite3 mismatches.db "SELECT timestamp, primaryAction, candidateAction FROM mismatches LIMIT 10;"
```

---

## Evaluation heuristics

For every completed shadow call the service applies two deterministic rules in order:

1. **Valid JSON** — `choices[0].message.content` from both models must parse as JSON.
   If either fails, the shadow is recorded as completed but not as a match.
2. **Exact action match** — the `action` key extracted from both JSON payloads must
   be string-equal. Partial or fuzzy matching is intentionally excluded.

Mismatches where both responses are valid JSON but actions differ are written to
`mismatches.db` asynchronously for offline inspection.

---

## Bounding memory footprint under load

Shadow tasks are managed by `ShadowPool` (`app/core/shadowPool.py`), a thin wrapper
around an atomic counter and `asyncio.Lock`.

```
MAX_CONCURRENT_SHADOWS (default 50)
        │
        ▼
  _active < max ──► accept task, increment _active, create_task(_wrap)
  _active >= max ──► close coroutine immediately, recordShed(), return
```

**Why this prevents memory exhaustion:**

- Each rejected coroutine is `.close()`d before being discarded — Python releases
  its frame immediately, no `ResourceWarning`, no heap growth.
- Accepted tasks decrement `_active` in a `finally` block, so a crashed task never
  leaks a slot.
- The queue depth is constant: `MAX_CONCURRENT_SHADOWS` tasks at most, each holding
  one open `httpx.AsyncClient` connection and one response buffer. Memory usage is
  `O(MAX_CONCURRENT_SHADOWS)` regardless of traffic volume.
- `shedCount` in `/metrics` surfaces how often the cap was hit, giving operators
  visibility to tune `MAX_CONCURRENT_SHADOWS` via `cloud.env` without a code change.

To stress-test the cap locally:

```bash
# Set a very tight cap
sed -i 's/MAX_CONCURRENT_SHADOWS=.*/MAX_CONCURRENT_SHADOWS=2/' cloud.env

# Blast 20 concurrent requests
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/v1/chat \
    -H "Content-Type: application/json" \
    -d '{"messages":[{"role":"user","content":"ping"}]}' &
done
wait
sleep 3 && curl -s http://localhost:8000/metrics | python3 -m json.tool
# shedCount will show how many shadows were dropped to protect the primary path
```

---

## Running tests

### Locally

```bash
# Install all dependencies (includes pytest and pytest-asyncio)
pip install -r requirements.txt

# Run the full suite
pytest tests/ -v --tb=short

# Run only unit tests
pytest tests/unit/ -v

# Run only integration tests
pytest tests/integration/ -v

# Run a single file
pytest tests/unit/test_shadowPool.py -v
```

**Test coverage by file:**

```
tests/unit/test_metricsService.py      10 tests — counters, snapshot, rate calc
tests/unit/test_shadowPool.py           5 tests — capacity, concurrency, slot teardown
tests/unit/test_configService.py        5 tests — update, snapshot, boundary values
tests/unit/test_extractAction.py       11 tests — JSON parsing, missing keys, edge cases
tests/integration/test_proxyRoute.py   12 tests — success path, shadow match/mismatch,
                                                   SQLite write, load shed, errors
tests/integration/test_metricsRoute.py  5 tests — response envelope, camelCase keys,
                                                   live counter reflection
tests/integration/test_configRoute.py  11 tests — validation, boundary rejection,
                                                   behavioural gating at 0% and 100%
```

> All LLM HTTP calls are mocked at the `_callLlm` level — no real API keys or
> network access are required to run the test suite.

---

## CI/CD

The pipeline is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
and runs automatically on every push and pull request to any branch.

### What the pipeline does

```
push / pull_request
        │
        ▼
┌───────────────────────────────────────┐
│  ubuntu-latest  ·  Python 3.13        │
│                                       │
│  1. actions/checkout@v4               │
│  2. actions/setup-python@v5           │
│     └─ pip cache keyed on             │
│        requirements.txt hash          │
│  3. pip install -r requirements.txt   │
│  4. pytest tests/ -v --tb=short       │
└───────────────────────────────────────┘
```

### Required env vars in CI

No real secrets are needed. The four required settings fields are injected as plain
env vars directly in the workflow — all LLM calls are mocked in the test suite so
nothing ever reaches the network:

```yaml
env:
  PRIMARY_LLM_BASE_URL: https://inference.do-ai.run/v1
  PRIMARY_LLM_MODEL: openai-gpt-oss-120b
  CANDIDATE_LLM_BASE_URL: https://inference.do-ai.run/v1
  CANDIDATE_LLM_MODEL: openai-gpt-oss-120b
```

If you ever add tests that require a real API key, add it as a GitHub Actions secret
and reference it in the workflow:

```yaml
env:
  PRIMARY_LLM_API_KEY: ${{ secrets.DO_API_KEY }}
```

### Adding the secret in GitHub

1. Go to **Settings → Secrets and variables → Actions** in your repository.
2. Click **New repository secret**.
3. Name: `DO_API_KEY`, value: your `doo_v1_...` token.
4. Reference it in the workflow via `${{ secrets.DO_API_KEY }}`.

### Checking pipeline status

```bash
# Using the GitHub CLI
gh run list --limit 5
gh run view          # latest run — shows per-step output
gh run view --log    # full log output
```

Or open the **Actions** tab in your GitHub repository.
