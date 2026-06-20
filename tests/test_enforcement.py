"""Day 3 integration: policy enforcement through the adapter (needs Postgres).

Proves the engine's decision actually governs the database: a blocked statement
never reaches Postgres, an allowed read runs, and an unbounded read comes back
capped by an injected LIMIT. Skips cleanly when the dev DB isn't up.
"""

import json
import os

import asyncpg
import pytest

from adapters.mcp_server import ShadowSession
from engine.audit import AuditLog
from engine.policy import Policy

DB_DSN = os.environ.get(
    "AGENT_DB_DSN",
    "postgresql://postgres:postgres@localhost:5433/pagila",
)


@pytest.fixture
async def make_session(tmp_path):
    """Factory: build a ShadowSession with a given policy on the real pool."""
    pools = []
    audits = []

    async def _make(policy):
        try:
            pool = await asyncpg.create_pool(
                dsn=DB_DSN, min_size=1, max_size=4, timeout=5
            )
        except (OSError, asyncpg.PostgresError) as exc:
            pytest.skip(f"dev Postgres not reachable at {DB_DSN} ({exc})")
        log = tmp_path / f"audit{len(pools)}.jsonl"
        audit = AuditLog(log)
        await audit.start()
        pools.append(pool)
        audits.append(audit)
        return ShadowSession(pool, audit, policy), log

    yield _make

    for audit in audits:
        await audit.stop()
    for pool in pools:
        await pool.close()


async def test_blocked_statement_never_touches_the_database(make_session):
    # A disallowed table that also does not exist: if the statement were executed
    # we'd get a Postgres "relation does not exist" error. Blocked => error is
    # None, proving we never ran it.
    sess, _ = await make_session(Policy(allowed_tables=frozenset({"film"})))
    res = await sess.run_query("SELECT * FROM secret_accounts")
    assert res["blocked"] is True
    assert res["error"] is None  # the DB was never asked
    assert res["rows"] == []
    assert any(v["reason_code"] == "TABLE_NOT_ALLOWED" for v in res["violations"])


async def test_allowed_read_runs(make_session):
    sess, _ = await make_session(Policy(allowed_tables=frozenset({"film"})))
    res = await sess.run_query("SELECT film_id FROM film WHERE film_id = 1")
    assert res["blocked"] is False
    assert res["row_count"] == 1
    assert res["rows"][0]["film_id"] == 1


async def test_injected_limit_caps_rows_returned(make_session):
    # actor has 200 rows; max_rows_read=5 must cap the result at 5.
    sess, _ = await make_session(
        Policy(allowed_tables=frozenset({"actor"}), max_rows_read=5)
    )
    res = await sess.run_query("SELECT * FROM actor")
    assert res["blocked"] is False
    assert res["row_count"] == 5


async def test_observe_mode_logs_decision_but_still_runs(make_session):
    # film is NOT allowed -> the decision is "block", but observe mode runs anyway.
    sess, log = await make_session(
        Policy(mode="observe", allowed_tables=frozenset({"actor"}))
    )
    res = await sess.run_query("SELECT film_id FROM film WHERE film_id = 1")
    assert res["blocked"] is False  # observe never blocks the response
    assert res["row_count"] == 1  # it actually ran
    # ...but the recorded decision shows it WOULD have been blocked.
    await sess._audit.stop()  # flush
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["decision"]["allowed"] is False
    assert entry["decision"]["violations"][0]["reason_code"] == "TABLE_NOT_ALLOWED"


async def test_observe_mode_does_not_rewrite_live_reads(make_session):
    # P1c regression: observe must run the ORIGINAL sql, not an injected LIMIT --
    # actor has 200 rows; observe with max_rows_read=5 must still return all 200.
    sess, _ = await make_session(
        Policy(mode="observe", allowed_tables=frozenset({"actor"}), max_rows_read=5)
    )
    res = await sess.run_query("SELECT * FROM actor")
    assert res["blocked"] is False
    assert res["row_count"] == 200  # NOT capped at 5
