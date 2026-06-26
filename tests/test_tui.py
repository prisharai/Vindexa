"""Slice 2: Human Mode TUI control flow (needs Postgres).

The visual rendering is rich's job; what matters to test is that the TUI honors
the gate -- a CONFIRM write does not run unless the human answers 'y', a BLOCK
write never runs, and \\undo reverts the last write. We drive the same entry
points the interactive loop uses (`_run_sql`, `_do_revert`) with the prompt
monkeypatched, against a real GuardedSession. Skips cleanly without a dev DB.
"""

import os

import asyncpg
import pytest

import adapters.tui as tui
from engine.policy import Policy
from engine.session import GuardedSession
from engine.simulate import SimulationConfig, load_unique_columns
from engine.undo import UndoConfig, UndoStore

DB_DSN = os.environ.get(
    "AGENT_DB_DSN", "postgresql://postgres:postgres@localhost:5433/pagila"
)

_POLICY = Policy(
    allowed_tables=frozenset({"_tui_test"}),
    simulation=SimulationConfig(
        enabled=True, precise=True, confirm_over_rows=100, block_over_rows=100_000
    ),
    undo=UndoConfig(enabled=True, block_non_reversible=False),
)


def _mk_session(pool, uniq, *, allow_override=False):
    return GuardedSession(
        pool,
        _POLICY,
        undo_store=UndoStore(_POLICY.undo),
        unique_columns=uniq,
        allow_override=allow_override,
    )


@pytest.fixture
async def sess():
    try:
        pool = await asyncpg.create_pool(dsn=DB_DSN, min_size=1, max_size=4, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"dev Postgres not reachable ({exc})")
    async with pool.acquire() as c:
        await c.execute("DROP TABLE IF EXISTS _tui_test")
        await c.execute("CREATE TABLE _tui_test (id int primary key, val text)")
        await c.execute(
            "INSERT INTO _tui_test SELECT g, 'v'||g FROM generate_series(1,1000) g"
        )
        uniq = await load_unique_columns(c)
    try:
        yield _mk_session(pool, uniq, allow_override=True)
    finally:
        async with pool.acquire() as c:
            await c.execute("DROP TABLE IF EXISTS _tui_test")
        await pool.close()


async def _count(sess) -> int:
    async with sess._pool.acquire() as c:
        return await c.fetchval("SELECT count(*) FROM _tui_test")


def _answers(monkeypatch, *replies):
    """Make tui.Prompt.ask return the given replies in order."""
    seq = iter(replies)
    monkeypatch.setattr(tui.Prompt, "ask", staticmethod(lambda *a, **k: next(seq)))


async def test_confirm_declined_does_not_run(monkeypatch, sess):
    _answers(monkeypatch, "n")  # user declines the confirmation
    history: list = []
    await tui._run_sql(sess, history, "DELETE FROM _tui_test WHERE id > 0")
    assert await _count(sess) == 1000  # untouched
    assert history == []


async def test_confirm_accepted_runs_and_is_undoable(monkeypatch, sess):
    _answers(monkeypatch, "y")  # user confirms
    history: list = []
    await tui._run_sql(sess, history, "DELETE FROM _tui_test WHERE id > 0")
    assert await _count(sess) == 0
    assert len(history) == 1  # write recorded with its undo id

    # \undo restores it.
    await tui._do_revert(sess, history, "\\undo")
    assert await _count(sess) == 1000
    assert history == []  # the undone write is dropped from history


async def test_blocked_write_never_runs(monkeypatch, sess):
    # No prompt should ever be reached for a blocked statement.
    _answers(monkeypatch, "y")
    history: list = []
    prop = await tui._run_sql(sess, history, "DELETE FROM _tui_test")  # block
    assert prop is not None and prop.verdict == "block"
    assert await _count(sess) == 1000
    assert history == []


async def test_override_runs_the_blocked_write(monkeypatch, sess):
    # A block is advice for a human: \override (then a 'y' confirm) runs it.
    history: list = []
    prop = await tui._run_sql(sess, history, "DELETE FROM _tui_test")
    assert prop.verdict == "block" and await _count(sess) == 1000

    _answers(monkeypatch, "y")  # confirm the override warning
    await tui._do_override(sess, history, prop)
    assert await _count(sess) == 0
    assert len(history) == 1  # still recorded + undoable

    # and the overridden write reverts cleanly
    _answers(monkeypatch, "y")
    await tui._do_revert(sess, history, "\\undo")
    assert await _count(sess) == 1000


async def test_override_declined_does_not_run(monkeypatch, sess):
    history: list = []
    prop = await tui._run_sql(sess, history, "DELETE FROM _tui_test")
    _answers(monkeypatch, "n")  # decline the override
    await tui._do_override(sess, history, prop)
    assert await _count(sess) == 1000
    assert history == []
