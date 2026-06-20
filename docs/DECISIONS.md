# DECISIONS.md — running decision log

> A chronological log of every non-trivial decision and its rationale. Append a
> new entry per decision; never rewrite history. Update this **every session**
> (CLAUDE.md §7, §9, §13). Each entry: date, the decision, why, and any
> latency/safety impact.

Format:

```
## YYYY-MM-DD — <short title>
**Decision:** what we chose.
**Why:** the rationale / alternatives considered.
**Latency/safety impact:** effect on the §4 budget or safety posture (or "none").
```

---

## 2026-06-19 — Project kickoff, Day 0 started
**Decision:** Begin Day 0 (Foundation). Created `docs/DESIGN.md` and
`docs/DECISIONS.md` stubs before writing any code. Building one vertical slice
at a time, in order, per CLAUDE.md §8.
**Why:** CLAUDE.md §7/§13 require reading the operating guide and establishing
the design + decision docs before code. Day 0's "Done when" depends on these
stubs existing alongside the repo skeleton, Docker Compose, and tooling.
**Latency/safety impact:** None yet (docs only). Latency-budget mindset recorded
in DESIGN.md §2 as required by Day 0 notes.

## 2026-06-19 — Seed dataset: Pagila + large generated tables
**Decision:** Use **Pagila** (Postgres port of the Sakila sample DB) for the
realistic relational schema, plus a couple of **large generated tables (a few
million rows each)** layered on top for blast-radius and benchmark realism.
**Why:** Pagila gives genuine relational structure — foreign keys, views,
multiple table types — which exercises the parser/classifier on realistic
shapes (joins, CTEs, `UPDATE ... FROM`, FK cascades). But Pagila's tables are
small, so simulation ("would hit 2.3M rows") and the §4 latency benchmarks
would be meaningless on it alone. The large generated tables supply real
volume. Together: real structure *and* real scale. Alternatives considered:
pure synthetic generator (no real relational realism) and pgbench's own schema
(too thin for classification variety).
**Latency/safety impact:** None on the request path (dev data only). The large
tables are what make the Day 7 latency proof and Day 4 simulation credible.

## 2026-06-19 — Env & dependency management: pyproject.toml + uv
**Decision:** Manage the Python env and dependencies with **`pyproject.toml` +
`uv`**. Pin `pglast`, `asyncpg`, `pytest`, `ruff`, and `black` there; commit the
`uv.lock` for reproducibility.
**Why:** `uv` gives a fast, reproducible lockfile and a single declarative
manifest. `pglast` (libpg_query binding) is the real Postgres parser required by
CLAUDE.md §6 — never regex/string matching. `asyncpg` is the fast async driver
for the no-blocking-I/O rule. `ruff`+`black` keep it clean from day one;
`pytest` runs tests in the same slice as code. Alternatives: plain
`venv`+`requirements.txt` (no lockfile) and poetry (slower, heavier).
**Latency/safety impact:** None directly. `asyncpg` chosen partly *for* the §4
budget (async, no blocking I/O on the engine path).

## 2026-06-20 — Day 0 seed crash diagnosed + fixed; smoke test closes Day 0
**Decision:** Root-caused the stalled Day 0 seed and finished the slice. The
init crashed mid-seed with `PANIC: could not fsync ... Input/output error`
during the 2M-row `metric_sample` insert — **not** a SQL bug, but the Docker
Desktop VM running out of disk while writing WAL (Pagila + the 3M-row
`app_event` had already loaded). Fix: reclaimed ~3 GB of Docker build cache
(`docker builder prune -af`), dropped the half-seeded volume
(`docker compose down -v`), and re-seeded clean. Added `tests/test_smoke.py`,
which connects via `asyncpg` and asserts real row counts for all four seeded
tables, and filled in `README.md` with quickstart + reset instructions.
**Why:** The init scripts only run on an empty data volume, so the partial seed
was a silent trap — a re-`up` would have skipped seeding and left the large
tables missing while looking healthy. The smoke test checks counts (not
`assert True`) precisely so this failure mode is caught automatically; it skips
gracefully when no DB is reachable so CI without Docker stays green. Chose the
conservative cleanup (build cache + this project's volume only) over a global
`docker system prune -af` to avoid touching unrelated images on the machine.
**Latency/safety impact:** None on the request path (dev tooling + data only).
Verified seed: film 1000, customer 599, app_event 3,000,000,
metric_sample 2,000,000. Day 0 "Done when" now fully met:
`docker compose up` → seeded Postgres, `pytest` green, README explains startup.

## 2026-06-20 — Day 1: pass-through MCP server + shadow-mode audit log
**Decision:** Built the Day 1 slice. Added the official **`mcp`** SDK and stood
up a `FastMCP` server (`adapters/mcp_server.py`) exposing one `run_query` tool
that forwards SQL to Postgres unchanged and returns the result — **shadow mode:
log everything, block nothing.** Four sub-decisions worth recording:

1. **Single-execution primitive: `conn.prepare(sql)` + `stmt.fetch()`.** Probed
   asyncpg and confirmed this runs the statement *once* and exposes BOTH the
   returned rows AND the command tag via `stmt.get_statusmsg()` (`"SELECT 3"`,
   `"UPDATE 5"`, `"CREATE TABLE"`). So we get affected-row counts for writes
   with **no string matching** (CLAUDE.md §6) and no double execution of a
   side-effecting statement. `execute()` gives the tag but discards rows;
   `fetch()` gives rows but discards the tag — prepare+fetch gives both.
2. **Audit log is sync-enqueue / async-write (`engine/audit.py`).** `record()`
   is a synchronous, non-blocking `Queue.put_nowait` on the query path; a single
   background consumer batches entries and writes JSONL via `asyncio.to_thread`
   so the disk write never blocks the event loop (and thus never adds latency to
   other in-flight queries). On a full queue we **drop + count** rather than
   block — logging is fail-open by design (§4: a query must never wait on a log).
3. **DB errors are captured and returned, not raised.** In shadow mode a failing
   statement yields a structured `{"error": ...}` and is still logged (the
   corpus wants failures too, §10). The agent gets the DB's own message verbatim.
4. **`ShadowSession` lives in the adapter for now, but is transport-agnostic.**
   It depends only on an asyncpg pool + `AuditLog`, so it's unit-testable without
   MCP. There is no policy engine yet; when Day 3 adds parse/classify/policy the
   *decision* moves into `engine/` and the adapter calls it — the transport layer
   never grows policy logic (§5).

Config (DSN, audit path, pool size) is env-overridable with docker-compose
defaults. Runtime `logs/` added to `.gitignore`. Tests in
`tests/test_pass_through.py` cover reads, writes/DDL affected-counts, error
capture, and that every statement (success and failure) reaches the audit log.
**Latency/safety impact:** Request-path cost is a pool acquire + one Postgres
round trip (the irreducible cost of running the query) plus one non-blocking
enqueue. No blocking I/O, no network, no LLM on the path. This is the
pass-through baseline the Day 7 benchmark measures against.

## 2026-06-20 — Day 2: parse + classify (observe-only)
**Decision:** Built `engine/parser.py` and `engine/classifier.py` and wired the
classification into the shadow-mode audit log. Everything is derived from the
`pglast` AST — never string matching (§6) — and it's observe-only: we describe
each statement, we don't decide on it yet.

* **Parser:** `parse(sql)` returns a `ParseResult(statements, error)`, LRU-cached
  (2048) on the raw SQL. It never raises onto the hot path — a syntax error
  becomes `error=...` with empty statements, so the request path is exception-
  free. Resolves the DESIGN.md "parse-cache strategy/eviction" open question:
  plain bounded LRU keyed on SQL text.
* **Classifier:** per-statement `StatementInfo` (kind, stmt_type, tables,
  columns, has_where, unbounded_write, nested_dml, touches_system_catalog) plus a
  `Classification` aggregate (statement_count, is_multi_statement, most-severe
  kind, parse_error). Kind severity order OTHER < READ < WRITE < DDL < UNKNOWN;
  a multi-statement batch takes its worst statement's kind.
* **Tricky cases handled** (the §8 Day 2 list + anti-evasion extras): CTE names
  excluded from real tables; `UPDATE...FROM` and sub-selects collect all tables;
  comments stripped by the real parser; multi-statement detected; system-catalog
  refs flagged (qualified `pg_catalog`/`information_schema` and unqualified
  `pg_*`). Extras: **`nested_dml`** catches a write hidden under a non-write top
  node (data-modifying CTE `WITH d AS (DELETE...) SELECT` — top node is a
  `SelectStmt` but it's a WRITE); **`unbounded_write`** flags any UPDATE/DELETE
  (top-level or nested) with no WHERE — exactly what Day 3 will block.
* **Parse failures fail safe:** `kind=UNKNOWN` + the parser message, no
  exception. UNKNOWN is the most-severe kind so writes fail closed downstream
  (§4). Enforcement is Day 3; Day 2 only records it.

**Latency/safety impact:** Adds parse + classify to the hot path. Measured on a
realistic CTE/UPDATE...FROM/sub-select statement: **0.168 ms cold (parse + walk),
~0.0001 ms warm (cache hit)** — ~30× under the §4 5 ms p99 budget cold, free when
cached. Both `parse` and `classify` are LRU-cached on the SQL text. No I/O, no
network, no blocking. The classification is added to the async audit entry only;
it changes no behavior and the pass-through result is unchanged.

## 2026-06-20 — Day 2 QA fixes (P1/P1/P2 from QA_REPORT.md)
**Decision:** Fixed three issues from QA review of the Day 2 classifier/parser,
each with a permanent regression test (§8 Day 8).

* **P1a — side-effecting statements no longer fall through to `OTHER`.** The
  classifier had a permissive `OTHER` fallback, so `DO`, `CALL`, `COPY` (incl.
  `COPY ... PROGRAM` shell-out), `LOCK`, `VACUUM`, `REINDEX`, `REFRESH
  MATERIALIZED VIEW`, `CREATE DATABASE`, `ALTER SYSTEM`, `EXECUTE`, etc. looked
  as harmless as `BEGIN`. Replaced it with an explicit safe-utility allowlist
  (`TransactionStmt`, `VariableSetStmt`, `VariableShowStmt`) → `OTHER`; every
  other/unrecognized top-level node **fails closed to `DDL` severity**.
  `UNKNOWN` stays reserved for parse failures. (`EXPLAIN` is intentionally not
  on the allowlist — `EXPLAIN ANALYZE` actually executes; Day 3 can refine.)
* **P1b — CTE filtering was scope-blind and could hide real tables.**
  `_real_tables` removed any unqualified RangeVar whose name appeared in a single
  global `cte_names` set, so a CTE in an inner subquery could suppress an outer
  real table (`SELECT * FROM secret WHERE EXISTS (WITH secret AS (...) ...)`
  returned no tables). Replaced with **scope-aware resolution**: each RangeVar is
  suppressed only if an *ancestor* statement's `WITH` clause defines its name
  (walked via the visitor's ancestor chain), and DML/MERGE **target relations are
  protected by node identity** so a same-named CTE can never hide a write target.
* **P2 — `parse()` could still raise on the hot path.** It caught only
  `ParseError`, but `pglast.parse_sql` raises e.g. `UnicodeEncodeError` on lone
  surrogates. Added a defensive `except Exception` that returns
  `ParseResult(error=...)` so malformed input becomes `UNKNOWN` instead of an
  exception — upholding the §4 non-raising-hot-path guarantee.

**Latency/safety impact:** Pure-classification changes; no I/O added. P1b's
per-RangeVar ancestor walk is negligible — cold classify still **0.167 ms (~30×
under the 5 ms p99 budget)**, warm unchanged. All three fixes make the classifier
*more* fail-closed (safer defaults), consistent with §4. 53 tests green.

<!-- Append future decisions below this line. -->
