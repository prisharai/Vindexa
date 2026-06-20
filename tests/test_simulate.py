"""Day 4 tests: blast-radius simulation (needs Postgres).

Verifies exact affected-row counts via BEGIN/ROLLBACK (and that the rollback
really leaves the table untouched), the cheap EXPLAIN-estimate path, hard
time-boxing with clean abort, and the gate that keeps simulation off reads and
routine traffic. Skips cleanly when the dev DB isn't up.
"""

import os

import asyncpg
import pytest

from engine.classifier import classify
from engine.simulate import SimulationConfig, is_risky_write, simulate

DB_DSN = os.environ.get(
    "AGENT_DB_DSN",
    "postgresql://postgres:postgres@localhost:5433/pagila",
)


@pytest.fixture
async def conn():
    try:
        c = await asyncpg.connect(dsn=DB_DSN, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"dev Postgres not reachable at {DB_DSN} ({exc})")
    try:
        yield c
    finally:
        await c.close()


ON = SimulationConfig(enabled=True, precise=True)


# --- Gating (pure, no DB) ----------------------------------------------------


def test_only_single_statement_writes_are_risky():
    assert is_risky_write(classify("UPDATE film SET rental_rate = 1 WHERE film_id=1"))
    assert is_risky_write(classify("DELETE FROM rental WHERE rental_id = 1"))
    assert not is_risky_write(classify("SELECT * FROM film"))  # read
    assert not is_risky_write(classify("DROP TABLE film"))  # ddl
    assert not is_risky_write(classify("UPDATE film SET x=1; SELECT 1"))  # multi


async def test_disabled_config_skips(conn):
    r = await simulate(
        conn,
        "UPDATE film SET rental_rate = 1 WHERE film_id = 1",
        classify("UPDATE film SET rental_rate = 1 WHERE film_id = 1"),
        SimulationConfig(enabled=False),
    )
    assert r.method == "skipped"


async def test_reads_are_not_simulated(conn):
    sql = "SELECT * FROM film"
    r = await simulate(conn, sql, classify(sql), ON)
    assert r.method == "skipped"


# --- Exact counts + rollback safety ------------------------------------------


async def test_exact_affected_rows_and_rollback_leaves_table_untouched(conn):
    # Scratch table with no FKs so the DELETE is clean; proves exact count and
    # that the rollback restores the table.
    await conn.execute("DROP TABLE IF EXISTS _sim_del_scratch")
    await conn.execute("CREATE TABLE _sim_del_scratch (id int)")
    await conn.execute("INSERT INTO _sim_del_scratch SELECT generate_series(1, 20)")
    try:
        sql = "DELETE FROM _sim_del_scratch WHERE id <= 9"
        r = await simulate(conn, sql, classify(sql), ON)
        assert r.method == "precise"
        assert r.exact_rows == 9  # measured the real blast radius
        assert r.affected_rows == 9
        # ...and the DELETE was rolled back -- the table is unchanged.
        assert await conn.fetchval("SELECT count(*) FROM _sim_del_scratch") == 20
    finally:
        await conn.execute("DROP TABLE IF EXISTS _sim_del_scratch")


async def test_update_exact_count(conn):
    sql = "UPDATE film SET rental_rate = rental_rate WHERE rental_rate < 3"
    expected = await conn.fetchval("SELECT count(*) FROM film WHERE rental_rate < 3")
    r = await simulate(conn, sql, classify(sql), ON)
    assert r.exact_rows == expected


async def test_simulation_surfaces_constraint_violation(conn):
    # Honest bonus: the precise path executes before rolling back, so it also
    # reveals a write that WOULD fail -- here a DELETE blocked by a foreign key.
    sql = "DELETE FROM rental WHERE rental_id < 10"
    r = await simulate(conn, sql, classify(sql), ON)
    assert r.exact_rows is None
    assert r.error is not None and "ForeignKeyViolation" in r.error
    # connection remains usable after the rolled-back failure
    assert await conn.fetchval("SELECT 1") == 1


# --- Estimate-only path (no execution, no locks) -----------------------------


async def test_estimate_only_mode(conn):
    sql = "UPDATE film SET rental_rate = 1 WHERE rental_rate < 3"
    r = await simulate(
        conn, sql, classify(sql), SimulationConfig(enabled=True, precise=False)
    )
    assert r.method == "estimate"
    assert r.exact_rows is None  # never executed
    assert r.estimated_rows is not None and r.estimated_cost is not None


# --- Time-boxing aborts cleanly ----------------------------------------------


async def test_statement_timeout_aborts_cleanly_and_rolls_back(conn):
    # A large write with a 10 ms cap must time out, report it, and leave the
    # table untouched -- never a partial commit.
    sql = "UPDATE app_event SET amount = amount WHERE customer_id < 600"
    before = await conn.fetchval("SELECT count(*) FROM app_event")
    r = await simulate(
        conn,
        sql,
        classify(sql),
        SimulationConfig(enabled=True, precise=True, statement_timeout_ms=10),
    )
    assert r.timed_out is True
    assert r.exact_rows is None
    after = await conn.fetchval("SELECT count(*) FROM app_event")
    assert after == before  # rolled back despite the timeout
    # The connection is still usable after a clean abort.
    assert await conn.fetchval("SELECT 1") == 1
