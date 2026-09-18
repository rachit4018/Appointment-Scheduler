# Appointment Scheduler

A patient/provider appointment booking app: request → confirm → cancel/reschedule,
with optimistic-concurrency conflict handling, an append-only audit trail, a
non-blocking confirmation notification, and a database-enforced guarantee against
double-booked providers.

FastAPI serves both the JSON API and the server-rendered UI (Jinja2 templates +
static JS/CSS under `backend/app/`) — there's no separate frontend to build or run.

## Prerequisites

- Docker (for Postgres, and optionally for the backend container)
- Python 3.12 for running the backend outside Docker

## Quickstart (Docker Compose)

```bash
docker compose up --build
```

This starts Postgres and the backend together. The backend's `CMD` runs
`alembic upgrade head` on boot, and the app seeds demo data on first startup.
Once healthy, open **http://localhost:8000**.

## Quickstart (backend on the host, Postgres in Docker)

Useful for local development and for running the test suite.

```bash
docker compose up -d db

cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql+psycopg://rachit4018:portal@localhost:5432/portal"
alembic upgrade head
uvicorn app.main:app --reload
```

Open **http://localhost:8000**. Seed data loads automatically on startup (it's a
no-op if users already exist).

## Running tests

Tests run against a real Postgres, since the double-booking guarantee is a
database constraint (`EXCLUDE USING gist`) — testing it against SQLite or a mock
would test something other than what ships.

```bash
docker compose up -d db
cd backend
DATABASE_URL="postgresql+psycopg://rachit4018:portal@localhost:5432/portal" python -m app.seed
DATABASE_URL="postgresql+psycopg://rachit4018:portal@localhost:5432/portal" pytest -v
```

## Demo users

There's no login — identity comes from a role switcher in the UI (an
`X-User-Id` header / `user_id` cookie). Seeded users:

| Role     | Name            |
|----------|-----------------|
| Patient  | Meera Shah      |
| Patient  | Arjun Rao       |
| Patient  | Priya Nair      |
| Provider | Dr. Anand Patel |
| Provider | Dr. Sara Iyer   |

## Project layout

```
backend/
  app/
    routers/        API routes (appointments, users)
    services/        Business logic: transitions, ownership, concurrency
    models.py         SQLAlchemy models
    schemas.py        Pydantic request/response models
    deps.py            Identity + role-based authorization
    notifications.py  Non-blocking confirmation notification (stub sender)
    templates/, static/  Server-rendered UI
  alembic/           Migrations (includes the overlap exclusion constraint)
  tests/             Integration tests against a real Postgres
docker-compose.yml
```
