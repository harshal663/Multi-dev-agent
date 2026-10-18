# MIO — Autonomous Multi-Agent Dev Team & CI/CD Sandbox

A four-agent dev team (**Architect → Coder → Code Reviewer → QA Tester**) built on
**LangGraph**, served by **FastAPI**, backed by **Redis** for chat/state
persistence, and executing generated code inside a **network-disabled Docker
sandbox**. The frontend is a single-page, Claude-inspired dashboard with a
sidebar chat list and an artifact-style code panel you can edit and send
back to the team.

## How it works

```
 User message
      │
      ▼
   intake ──(fresh request)──► architect ──► coder ──► reviewer ──┐
      │                                         ▲                  │
      └─(edit_suggestion present)──► coder ◄────┴── blockers ──────┘
                                          │                 │
                                          │              clean
                                          ▼                 ▼
                                        (loop)             qa ──pass──► END
                                                             │
                                                          fail, cycles left
                                                             │
                                                             ▼
                                                           coder (loop)
```

- **Architect** turns a request into a short plan + file list.
- **Coder** writes full file contents (and reconciles any manual user edit).
- **Code Reviewer** flags blockers; blockers send it back to the Coder.
- **QA Tester** gives the final pass/fail verdict; failures loop back to the
  Coder up to `MIO_MAX_REVIEW_CYCLES` times.
- Every node's output streams to the frontend live over SSE.
- The **sandbox** (`app/sandbox/docker_executor.py`) can run generated
  Python/tests in an ephemeral, network-disabled container — wired up and
  ready to call from the QA node once you want agents to actually execute
  code rather than just reason about it (see "Extending" below).

## Project layout

```
mio-devteam/
├── backend/
│   ├── app/
│   │   ├── main.py           FastAPI app, static frontend mount
│   │   ├── config.py         Settings (env vars, MIO_ prefix)
│   │   ├── models.py         Pydantic schemas
│   │   ├── redis_client.py   Session persistence
│   │   ├── agents/
│   │   │   ├── state.py      LangGraph state (TypedDict)
│   │   │   ├── nodes.py      architect/coder/reviewer/qa node functions
│   │   │   ├── graph.py      Graph wiring + revision-loop routing
│   │   │   └── llm.py        Claude client wrapper
│   │   ├── sandbox/
│   │   │   └── docker_executor.py   Ephemeral, sandboxed code execution
│   │   └── routes/
│   │       ├── chat.py       Session + message + SSE streaming endpoints
│   │       └── files.py      Single-file fetch endpoint
│   ├── requirements.txt
│   ├── Dockerfile
│   └── smoke_test.py         End-to-end test (mocked LLM, real Redis/FastAPI)
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── sandbox/
│   └── Dockerfile             Image the code-execution sandbox runs
├── docker-compose.yml
└── .env.example
```

## Running it

### Option A — Docker Compose (recommended)

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

Open http://localhost:8000.

> `docker-compose.yml` builds the sandbox image, starts Redis, and mounts
> the host Docker socket into the backend container so it can launch sandbox
> containers as siblings (not nested Docker-in-Docker).

### Option B — Local dev (no Docker for the app itself)

```bash
# 1. Redis
redis-server &

# 2. Backend
cd backend
pip install -r requirements.txt
export MIO_ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload --port 8000

# 3. (optional) build the sandbox image so code-execution works
docker build -t mio-sandbox-python:latest sandbox/
```

Open http://localhost:8000 — the backend serves the frontend directly.

### Verifying the backend end-to-end

`backend/smoke_test.py` exercises the real FastAPI app, the real LangGraph
graph, and a real Redis instance (with Claude calls mocked, so it needs no
API key or network access):

```bash
cd backend
python3 smoke_test.py
```

It walks: create session → send a message → full architect→coder→reviewer→qa
run → verify persistence → submit a user edit-suggestion → verify the
revision cycle bumps the file version → delete the session.

## Frontend

- **Sidebar** — "MIO" branding, **New chat**, and a saved-chat list (title,
  message count, last-updated) pulled from `GET /api/sessions`.
- **Chat thread** — each agent's turn is labeled and color-coded (Architect
  blue, Coder green, Reviewer amber, QA purple); files an agent produces
  appear as clickable chips under its message.
- **Artifact panel** — clicking a file chip slides in a right-hand panel
  (tabs across files, editable code area). Editing and clicking
  **"Suggest edit to agent"** posts to `POST /api/sessions/{id}/files/edit`,
  which re-enters the graph at the Coder node with your edit and note, then
  streams the revision cycle back into the same chat.

No build step — it's vanilla HTML/CSS/JS served directly by FastAPI's
`StaticFiles`.

## Extending

- **Wire the sandbox into QA**: call `run_python_tests()` from
  `app/sandbox/docker_executor.py` inside `qa_node()` (`app/agents/nodes.py`)
  to actually execute generated code/tests instead of only reasoning about
  correctness, and fold the pass/fail + stdout into the QA verdict.
- **CI/CD**: the same sandbox executor is the natural hook for a "run on
  every Coder commit" pipeline — call it from the Coder node too, and feed
  failures back as review notes.
- **Auth / multi-user**: sessions are keyed only by session ID today; add a
  user ID prefix to the Redis keys in `redis_client.py` if you need
  per-account isolation.
- **Swap models per role**: each agent has its own `MIO_*_MODEL` env var, so
  you can e.g. run the Architect on a stronger model and the Reviewer on a
  cheaper one.

## Notes & limitations

- The LLM calls use `langchain-anthropic`; you need a real `ANTHROPIC_API_KEY`
  for the agents to actually run (the smoke test mocks this out).
- The Docker sandbox executor requires a reachable Docker daemon (via
  `docker-compose`'s socket mount, or a local Docker install) — it raises a
  clear `SandboxUnavailable` error rather than crashing if the daemon isn't
  reachable, so the rest of the app keeps working without it.
- Chat/file state lives in Redis with a 7-day TTL (`MIO_SESSION_TTL_SECONDS`
  equivalent — see `settings.session_ttl_seconds`); there's no separate
  database, by design, to keep the stack small.
