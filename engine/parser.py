"""SQL -> AST parsing via ``pglast`` (libpg_query, the real Postgres parser).

HOT PATH. Every agent statement is parsed here while the agent waits, so this
must be fast and cached (CLAUDE.md sec. 4). Never classify by string matching --
always operate on the AST (sec. 6). Parse failures fail closed for writes
(decided downstream in the policy engine; here we just surface the failure).

Why ``pglast``: it binds libpg_query, the *actual* Postgres parser, so it sees
statements exactly as the server will -- comments stripped, multi-statement
splits, dialect quirks and all. A regex never could (sec. 6).

Latency: ``parse_sql`` measures ~0.1 ms cold on a realistic statement, and we
cache results, so repeats are effectively free. The cache is a bounded LRU keyed
on the raw SQL text; eviction is least-recently-used (an open question in
DESIGN.md, resolved here as plain LRU -- simplest thing that bounds memory).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pglast import parse_sql
from pglast.parser import ParseError

# Bound the parse cache so memory can't grow without limit under varied traffic.
# Agent SQL often varies by literal, so the hit rate isn't 100%, but identical
# repeats (very common with agents looping/retrying) become ~free.
_PARSE_CACHE_SIZE = 2048

# Hard ceiling on input size, checked BEFORE any parse work (sec. 4). libpg_query
# is super-linear on huge value lists (a 40 KB IN-list takes ~400 ms), which would
# blow the p99 budget. Real agent SQL is well under this; anything larger is
# pathological and fails closed instantly with an O(1) length check -- the block
# is sub-millisecond. (Not a per-query <5 ms guarantee for everything under the
# cap; it's a fail-safe against unbounded blowup. Common traffic is unaffected.)
_MAX_SQL_BYTES = 8000


@dataclass(frozen=True)
class ParseResult:
    """Outcome of parsing one input string (which may hold several statements).

    ``statements`` is a tuple of ``pglast`` ``RawStmt`` nodes (empty on error).
    ``error`` is ``None`` on success, else the parser's message. We never raise
    onto the hot path -- a parse failure is data, handled by the caller (writes
    fail closed in the policy engine, sec. 4).
    """

    statements: tuple
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


@lru_cache(maxsize=_PARSE_CACHE_SIZE)
def parse(sql: str) -> ParseResult:
    """Parse ``sql`` to an AST. Cached, non-raising. Hot-path safe.

    Returns a :class:`ParseResult`. On a syntax error we capture the message and
    return an empty statement tuple rather than raising, so the request path
    stays branch-free of exceptions.
    """
    if len(sql) > _MAX_SQL_BYTES:
        # Fail closed BEFORE parsing -- the cheap O(1) guard that protects the
        # latency budget from pathological inputs (sec. 4).
        return ParseResult(
            statements=(),
            error=f"input too large ({len(sql)} bytes > {_MAX_SQL_BYTES} limit)",
        )
    try:
        raw = parse_sql(sql)
    except ParseError as exc:
        return ParseResult(statements=(), error=str(exc))
    except Exception as exc:
        # Defensive: libpg_query can raise beyond ParseError for malformed input
        # -- e.g. UnicodeEncodeError on lone surrogates, or recursion limits on
        # pathological nesting. The hot path must never raise (sec. 4): turn any
        # such failure into data so the statement classifies as UNKNOWN and
        # writes fail closed downstream.
        return ParseResult(statements=(), error=f"{type(exc).__name__}: {exc}")
    return ParseResult(statements=tuple(raw), error=None)


def cache_clear() -> None:
    """Clear the parse cache (tests; or a future policy/schema reload)."""
    parse.cache_clear()
