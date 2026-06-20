# Agent DB Safety

A runtime safety layer that sits between AI agents and a database (Postgres
first). Every statement an agent issues is parsed, classified, and checked
against a deterministic policy; risky writes are **simulated to measure their
blast radius** before they commit and **recorded so they can be instantly
undone**; blocked statements come back with a structured, machine-readable
explanation so the agent can self-correct. The load-bearing safety is
deterministic and fast — see [`CLAUDE.md`](CLAUDE.md) for the full design intent
and [`docs/DESIGN.md`](docs/DESIGN.md) for the architecture.

> Status: **Day 0 (Foundation)** — repo skeleton, tooling, and a seeded local
> Postgres. The engine and MCP adapter are stubs being filled in slice by slice
> (see the build plan in `CLAUDE.md` §8).

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (Compose v2)
- [`uv`](https://docs.astral.sh/uv/) for the Python env

## Quickstart

```bash
# 1. Start a seeded local Postgres (Pagila sample DB + two large generated
#    tables for blast-radius / benchmark realism). Listens on host port 5433.
docker compose up -d

# 2. Install the Python env (runtime + dev deps) from the lockfile.
uv sync

# 3. Run the tests. The smoke test connects to the DB above and verifies the
#    seed is complete; it SKIPS cleanly if the DB isn't up.
uv run pytest
```

First `docker compose up` generates ~5M rows in the large tables and takes
roughly 1–2 minutes. Subsequent starts are instant — the data lives in a Docker
volume.

## Connecting to the database

| Setting  | Value      |
| -------- | ---------- |
| Host     | `localhost`|
| Port     | `5433`     |
| User     | `postgres` |
| Password | `postgres` |
| Database | `pagila`   |

```bash
# psql via the container:
docker exec -it agent-db-safety-pg psql -U postgres -d pagila

# or any client over the DSN:
postgresql://postgres:postgres@localhost:5433/pagila
```

The test suite reads `AGENT_DB_DSN` if set, otherwise uses the DSN above.

## Resetting the database

The data dir resets freely — never assume persistence between runs. The seed
scripts in `db/` only run on a *fresh* volume, so to re-seed from scratch:

```bash
docker compose down -v   # drops the data volume
docker compose up -d     # re-runs the seed
```

## Repo layout

```
engine/      # transport-agnostic safety core: parse, classify, policy, simulate, undo, audit
adapters/    # MCP server (Phase A); wire-protocol proxy (Phase B, later)
policies/    # declarative YAML policy files
corpus/      # red (should-block) + green (should-allow) query sets
benchmarks/  # latency-budget harness (Day 7)
db/          # Docker Postgres seed scripts (Pagila + large tables)
docs/        # DESIGN.md (living design) + DECISIONS.md (decision log)
tests/       # pytest suite, written alongside each slice
```
