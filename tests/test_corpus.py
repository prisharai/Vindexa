"""Day 3 corpus harness: red (should-block) and green (should-allow).

Evaluates the committed corpora against the committed ``policies/default.yaml`` --
so this is a regression test on the *real* shipped policy, not a hand-built one.

* Every RED query must be blocked, and must surface its expected reason code.
  A red leak (false negative) is the worst failure mode (CLAUDE.md sec. 8 Day 8).
* Every GREEN query must be allowed. A green block (false positive) erodes trust
  and gets the tool disabled (sec. 11).
"""

from pathlib import Path

import pytest
import yaml

from engine.policy import Policy, decide

_ROOT = Path(__file__).resolve().parent.parent
POLICY = Policy.load(_ROOT / "policies" / "default.yaml")

RED = yaml.safe_load((_ROOT / "corpus" / "red" / "queries.yaml").read_text())
GREEN = yaml.safe_load((_ROOT / "corpus" / "green" / "queries.yaml").read_text())


@pytest.mark.parametrize("entry", RED, ids=[e["sql"][:40] for e in RED])
def test_red_corpus_is_blocked(entry):
    d = decide(entry["sql"], POLICY)
    assert not d.allowed, f"RED LEAK (false negative): {entry['sql']!r}"
    codes = {v.reason_code for v in d.violations}
    assert (
        entry["expect"] in codes
    ), f"blocked, but not for the expected reason {entry['expect']}: got {codes}"


@pytest.mark.parametrize("entry", GREEN, ids=[e["sql"][:40] for e in GREEN])
def test_green_corpus_is_allowed(entry):
    d = decide(entry["sql"], POLICY)
    assert d.allowed, (
        f"GREEN FALSE POSITIVE: {entry['sql']!r} blocked by "
        f"{[v.reason_code for v in d.violations]}"
    )


def test_corpora_are_nonempty_and_disjoint():
    red_sql = {e["sql"] for e in RED}
    green_sql = {e["sql"] for e in GREEN}
    assert red_sql and green_sql
    assert red_sql.isdisjoint(green_sql)
