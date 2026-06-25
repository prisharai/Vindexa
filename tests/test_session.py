"""Slice 1: the interactive propose -> confirm -> execute gate (needs Postgres).

Covers the contract a UI relies on:
* a safe, scoped write gets an ``allow`` verdict and runs;
* a risky write whose blast radius exceeds ``confirm_over_rows`` gets a
  ``confirm`` verdict and is NOT run until ``force=True``;
* a policy violation gets a ``block`` verdict and never runs, even with force;
* execute honors the verdict (the engine, not the UI, enforces this);
* an executed write is reversible via the session's revert handle;
* the mid-statement-semicolon footgun is blocked (regression for the
  ``DELETE FROM t; WHERE id = 9`` class that silently hits the whole table).

Skips cleanly when the dev DB isn't up.
"""

import os

import asyncpg
import pytest

from engine.policy import Policy
from engine.session import ALLOW, BLOCK, CONFIRM, GuardedSession
from engine.simulate import SimulationConfig, load_unique_columns
from engine.undo import UndoConfig, UndoStore

DB_DSN = os.environ.get(
    "AGENT_DB_DSN",
    "postgresql://postgres:postgres@localhost:5433/pagila",
)

# Confirm above 100 affected rows; block above 100k. The scratch table has 1000
# rows so a full-table write (>100) gates, a 1-row write does not.
_POLICY = Policy(
    allowed_tables=frozenset({"_sess_test"}),
    simulation=SimulationConfig(
        enabled=True, precise=True, confirm_over_rows=100, block_over_rows=100_000
    ),
    undo=UndoConfig(enabled=True, block_non_reversible=False),
)


@pytest.fixture
async def sess():
    try:
        pool = await asyncpg.create_pool(dsn=DB_DSN, min_size=1, max_size=4, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"dev Postgres not reachable at {DB_DSN} ({exc})")
    async with pool.acquire() as c:
        await c.execute("DROP TABLE IF EXISTS _sess_test")
        await c.execute(
            "CREATE TABLE _sess_test (id int primary key, val text, active boolean)"
        )
        await c.execute(
            "INSERT INTO _sess_test "
            "SELECT g, 'v'||g, true FROM generate_series(1,1000) g"
        )
        uniq = await load_unique_columns(c)
    store = UndoStore(_POLICY.undo)
    try:
        yield GuardedSession(
            pool, _POLICY, undo_store=store, unique_columns=uniq
        )
    finally:
        async with pool.acquire() as c:
            await c.execute("DROP TABLE IF EXISTS _sess_test")
        await pool.close()


async def _count(sess) -> int:
    async with sess._pool.acquire() as c:
        return await c.fetchval("SELECT count(*) FROM _sess_test")


# --- verdicts ----------------------------------------------------------------


async def test_scoped_write_allows_and_runs(sess):
    prop = await sess.propose("UPDATE _sess_test SET val = 'x' WHERE id = 5")
    assert prop.verdict == ALLOW
    res = await sess.execute(prop)
    assert res.executed and res.error is None


async def test_full_table_write_requires_confirmation(sess):
    prop = await sess.propose("DELETE FROM _sess_test WHERE active = true")
    assert prop.verdict == CONFIRM
    # blast radius is measured and surfaced for the UI
    assert prop.blast_radius == 1000
    assert prop.blast_method == "precise"

    # Without confirmation, nothing runs and the table is untouched.
    refused = await sess.execute(prop)
    assert not refused.executed and refused.refused == "needs_confirmation"
    assert await _count(sess) == 1000

    # The human confirms -> it runs.
    ok = await sess.execute(prop, force=True)
    assert ok.executed and ok.error is None
    assert await _count(sess) == 0


async def test_blocked_write_never_runs_even_with_force(sess):
    # No WHERE -> WRITE_WITHOUT_WHERE violation, blocked before the DB.
    prop = await sess.propose("DELETE FROM _sess_test")
    assert prop.verdict == BLOCK
    assert prop.violations and prop.violations[0]["reason_code"]
    res = await sess.execute(prop, force=True)  # force must NOT override a block
    assert not res.executed and res.refused == "blocked"
    assert await _count(sess) == 1000


async def test_executed_write_is_reversible(sess):
    prop = await sess.propose("UPDATE _sess_test SET val = 'gone' WHERE id = 7")
    res = await sess.execute(prop)
    assert res.executed and res.action_id and res.reversible
    async with sess._pool.acquire() as c:
        assert await c.fetchval("SELECT val FROM _sess_test WHERE id = 7") == "gone"
    rev = await sess.revert(res.action_id)
    assert rev.ok
    async with sess._pool.acquire() as c:
        assert await c.fetchval("SELECT val FROM _sess_test WHERE id = 7") == "v7"


# --- the semicolon footgun (regression) --------------------------------------


async def test_mid_statement_semicolon_is_blocked(sess):
    # 'DELETE FROM t; WHERE id = 9' parses as TWO statements: a full-table
    # DELETE followed by a stray fragment. The classic footgun that silently
    # wipes the table. Must be blocked, never previewed as a safe point delete.
    prop = await sess.propose("DELETE FROM _sess_test; WHERE id = 9")
    assert prop.verdict == BLOCK
    res = await sess.execute(prop, force=True)
    assert not res.executed
    assert await _count(sess) == 1000
