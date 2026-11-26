# AI Agent Platform

An API-first AI chat backend built incrementally with FastAPI. It combines
JWT authentication, PostgreSQL persistence, Redis rate limiting, Groq-hosted
LLMs through LangChain, Server-Sent Events (SSE), and a test suite that uses
fake LLM and Redis implementations.

This repository is designed to be studied by phase. The `main` branch contains
the latest implementation, while the other branches capture earlier milestones
and can be checked out independently.

## What the project demonstrates

- Registration and login with bcrypt password hashing and JWT bearer tokens
- Per-user CRUD for conversations
- Cascading PostgreSQL models for users, conversations, and messages
- Async SQLAlchemy 2.x and Alembic database migrations
- Groq chat completions through LangChain
- Conversation history supplied to the model on each turn
- Automatic conversation titles
- A normal JSON chat endpoint and an SSE streaming endpoint
- Per-user, per-minute Redis rate limiting
- Partial-response saving when an SSE client disconnects or streaming fails
- Dependency injection and focused tests with HTTPX ASGITransport

The project currently has a backend API, not a browser or mobile UI. Swagger UI
is available at `http://localhost:8000/docs` when the server is running.

## Architecture

```text
Client
  |
  | Authorization: Bearer <JWT>
  v
FastAPI routes
  |-- /auth              register, login, current user
  |-- /conversations     CRUD for conversations
  |-- /messages          normal and SSE chat responses
  |
  +--> Redis             chat rate limits
  |
  +--> PostgreSQL        users, conversations, messages
  |
  +--> LangChain Groq    generated chat replies

PostgreSQL and Redis (and optional Qdrant) can be started with Docker Compose.
```

Key implementation areas:

| Area | Files |
|---|---|
| Application startup | `backend/app/main.py` |
| Authentication | `backend/app/api/auth.py`, `backend/app/core/security.py` |
| Request dependencies | `backend/app/api/deps.py` |
| Conversations and chat | `backend/app/api/conversations.py` |
| Redis rate limiting | `backend/app/api/rate_limit.py`, `backend/app/core/redis.py` |
| Database models | `backend/app/db/models.py` |
| Async database session | `backend/app/db/session.py` |
| Chat persistence and title logic | `backend/app/services/chat.py` |
| Groq/LangChain integration | `backend/app/services/llm.py` |
| API schemas | `backend/app/schemas/` |
| Configuration | `backend/app/core/config.py`, `backend/.env.example` |
| Database migration | `backend/alembic/versions/20ed86073f53_initial_schema.py` |
| Tests | `backend/tests/` |

There is also a Qdrant service in Docker Compose. It is prepared as supporting
infrastructure, but the current chat flow does not yet use Qdrant retrieval.

## Branch guide

The repository's history is a useful learning trail:

| Branch | Milestone |
|---|---|
| `main` | Latest state: chat, SSE streaming, Redis limiting, and tests |
| `phase-1-api-db` | Project scaffold, Docker infrastructure, PostgreSQL models, JWT auth, and conversation CRUD |
| `phase-2-groq-chat` | Adds the Groq/LangChain model service to the API/database foundation |
| `phase-2-streaming` | Adds SSE streaming, Redis limits, and fake-provider tests |

Explore the history without installing extra tools:

```bash
git fetch --all
git branch -a
git log --oneline --decorate --graph --all
git diff phase-1-api-db..phase-2-groq-chat
git diff phase-2-groq-chat..phase-2-streaming
```

To study a branch in a clean, isolated workspace:

```bash
git switch --detach origin/phase-1-api-db
```

After trying a phase, return to the latest code:

```bash
git switch main
```

## Prerequisites

- Python 3.11 or newer
- Docker and Docker Compose
- A Groq API key for live chat responses
- `curl` or an API client such as Postman

## Run locally

Start PostgreSQL, Redis, and Qdrant:

```bash
docker compose up -d
docker compose ps
```

Create a Python environment and install backend dependencies:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` and set:

```dotenv
GROQ_API_KEY=your-groq-api-key
JWT_SECRET=choose-a-long-random-development-secret
DATABASE_URL=postgresql+asyncpg://agent:agent@localhost:5432/agent
REDIS_URL=redis://localhost:6379/0
QDRANT_URL=http://localhost:6333
```

Apply the migration:

```bash
alembic upgrade head
```

Start the API:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs`.

## Try the API

Register a user:

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"learner@example.com","password":"password123"}'
```

Log in and retain the returned token:

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=learner@example.com&password=password123"
```

Logout is client-side because access tokens are stateless. Call `POST /auth/logout`
for guidance, then remove the token from client storage; this endpoint does not
revoke tokens that have already been issued. Tokens remain valid until they
expire.

Login and registration are rate-limited per client IP to 10 and 5 attempts per
minute by default. Override these limits with
`AUTH_LOGIN_RATE_LIMIT_PER_MINUTE` and `AUTH_REGISTER_RATE_LIMIT_PER_MINUTE`.
If Redis is unavailable, authentication requests are allowed and the limiter
logs a warning.

Use `access_token` from the login response in the following examples:

```bash
export TOKEN="<access-token>"

curl -X POST http://localhost:8000/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"New chat"}'
```

Copy the returned conversation `id`, then send a normal chat request:

```bash
curl -X POST "http://localhost:8000/conversations/<conversation-id>/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"Explain fast dependency injection"}'
```

For streamed output:

```bash
curl -N -X POST \
  "http://localhost:8000/conversations/<conversation-id>/messages/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"Give me three concise learning tips"}'
```

SSE frames use `event: token` for text chunks and `event: done` when the full
assistant message has been saved.

## Tests

The test suite creates an isolated `agent_test` PostgreSQL database and replaces
the real LLM and Redis dependencies with deterministic fakes. Create the database
before the first test run:

```bash
docker exec ai-agent-postgres-1 psql -U agent -d postgres \
  -c "CREATE DATABASE agent_test;"
```

The container name may differ; verify it with `docker compose ps`.

Run tests from `backend`:

```bash
pytest -q
```

Tests cover:

- Registration, login, JWT protection, and password handling
- Conversation ownership and CRUD behavior
- Chat history, title generation, normal replies, and SSE streaming
- Redis rate limiting and expected 429 responses
- LLM and streaming error behavior

## How to approach this project

1. **Start with the data model.** Read `models.py` and the initial Alembic
   migration. Trace `User -> Conversation -> Message`, cascade deletes, UUID
   keys, and timestamps.
2. **Follow one HTTP request.** Start with registration, then login. Read the
   router, Pydantic schema, database session, password helper, and JWT
   dependency in that order.
3. **Study ownership.** In `conversations.py`, inspect
   `get_owned_conversation` and every conversation route. Notice that it joins
   ownership to every lookup instead of trusting a client-supplied user ID.
4. **Trace a normal chat turn.** Follow `prepare_turn`, `add_message`,
   `load_history`, `to_lc_messages`, `ainvoke`, and the final database save.
   Observe why the code commits before a slow LLM call.
5. **Compare normal and streaming chat.** Study `stream_message`, `sse`,
   `save_partial`, and cancellation handling. Then decide when JSON is a better
   fit than SSE.
6. **Study rate limiting.** Map the Redis key
   `rl:chat:<user-id>:<time-window>` and fixed 60-second window. Discuss what
   changes under Redis failures and distributed deployment.
7. **Use the branches as diffs, not just snapshots.** Rebuild each milestone
   mentally, then compare commits. Reproduce the code in a separate scratch
   branch without copying the final implementation blindly.
8. **Run and extend the tests.** First read `conftest.py` to understand
   dependency overrides. Then change a requirement and add the smallest failing
   test before modifying code.

## Good learning exercises

- Add pagination and sorting to conversation listing.
- Record failed login attempts or expose an account lockout policy.
- Add refresh tokens and a configurable JWT lifetime.
- Replace fixed-window rate limiting with a sliding-window algorithm.
- Add Redis or database tests for concurrent chat turns.
- Track conversation usage by token count.
- Add an integration that actually retrieves context from Qdrant.
- Add a cancellation test for streamed client disconnects.
- Introduce structured logs and request IDs.
- Add a small frontend and keep it as a separate project layer.

## Suggested code-reading order

For the first pass, read in this order:

1. `docker-compose.yml`
2. `backend/app/core/config.py`
3. `backend/app/db/models.py`
4. `backend/alembic/versions/20ed86073f53_initial_schema.py`
5. `backend/app/api/auth.py`
6. `backend/app/api/deps.py`
7. `backend/app/api/conversations.py`
8. `backend/app/services/chat.py`
9. `backend/app/services/llm.py`
10. `backend/app/api/rate_limit.py`
11. `backend/tests/conftest.py`
12. `backend/tests/test_chat.py`

## Maintenance notes

- Keep secrets out of Git. `backend/.env` is intentionally local configuration;
  only `backend/.env.example` should be committed.
- `docker-compose.yml` starts infrastructure only; the FastAPI server is run
  separately with Uvicorn.
- Live Groq calls require network access and a valid API key.
- The test suite uses fakes, so it can verify application behavior without a
  real provider or Redis server.