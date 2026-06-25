"""Human Mode: an interactive, guarded SQL terminal (Slice 2).

A polished terminal UI for a *person* writing SQL by hand -- the "I write SQL"
half of the landing screen. Every statement goes through the same engine the
agents do: it's parsed, policy-checked, and -- for a risky write -- simulated so
the blast radius is shown *before* anything runs. Destructive writes require an
explicit keystroke to confirm; every executed write prints an undo id you can
revert with one command.

This module is pure rendering + input over ``engine.session.GuardedSession``. No
policy / simulation / undo logic lives here. Swap ``rich`` for a web frontend
later and the engine is untouched (CLAUDE.md sec. 5). ``rich`` is UI-only and is
never imported by ``engine/`` or anywhere near the request path (sec. 4).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from engine.audit import AuditLog
from engine.policy import Policy
from engine.session import BLOCK, CONFIRM, GuardedSession
from engine.simulate import load_unique_columns
from engine.undo import UndoStore

DB_DSN = os.environ.get(
    "AGENT_DB_DSN", "postgresql://postgres:postgres@localhost:5433/pagila"
)
POLICY_PATH = os.environ.get(
    "AGENT_POLICY",
    str(Path(__file__).resolve().parent.parent / "policies" / "default.yaml"),
)
AUDIT_LOG_PATH = os.environ.get("AGENT_AUDIT_LOG", "logs/audit.jsonl")

console = Console()


def _fmt_rows(n: int | None) -> str:
    return "unknown" if n is None else f"{n:,}"


# --- rendering ---------------------------------------------------------------


def _render_block(prop) -> None:
    """A policy violation: red, with the machine-readable reason + suggested fix."""
    body = Text()
    body.append(prop.sql + "\n\n", style="bold white")
    for v in prop.violations:
        body.append(f"✗ {v['reason_code']}\n", style="bold red")
        body.append(f"  {v['message']}\n", style="red")
        if v.get("suggested_fix"):
            body.append(f"  fix: {v['suggested_fix']}\n", style="yellow")
    console.print(
        Panel(body, title="⛔ BLOCKED", border_style="red", title_align="left")
    )


def _render_confirm(prop) -> None:
    """A risky write: amber panel showing the measured blast radius."""
    rows = prop.blast_radius
    method = prop.blast_method or "?"
    reversible = prop.is_write and not prop.blast_timed_out
    body = Text()
    body.append(prop.effective_sql + "\n\n", style="bold white")
    body.append("Blast radius: ", style="white")
    body.append(f"{_fmt_rows(rows)} rows", style="bold yellow")
    body.append(f"  ({method})\n", style="dim")
    if prop.blast_timed_out:
        body.append(
            "⚠ simulation timed out — true impact could not be bounded\n",
            style="bold red",
        )
    body.append("Reversible: ", style="white")
    body.append(
        "yes — an undo id will be kept\n" if reversible else "see result\n",
        style="green" if reversible else "yellow",
    )
    console.print(
        Panel(
            body,
            title="⚠ CONFIRM WRITE",
            border_style="yellow",
            title_align="left",
        )
    )


def _render_result(res) -> None:
    if res.refused:
        console.print(f"[dim]· not executed ({res.refused})[/dim]")
        return
    if res.error:
        console.print(f"[bold red]error:[/bold red] {res.error}")
        return
    if res.rows:
        _render_table(res.rows)
    status = res.status or "ok"
    line = f"[green]✓[/green] {status}"
    if res.action_id:
        line += (
            f"   [dim]undo id[/dim] [bold cyan]{res.action_id[:8]}[/bold cyan]"
            "  [dim](\\undo to revert)[/dim]"
        )
    elif res.reversible is False and res.undo_reason:
        line += f"   [yellow]not reversible: {res.undo_reason}[/yellow]"
    console.print(line)


def _render_table(rows: list[dict], limit: int = 20) -> None:
    cols = list(rows[0].keys())
    table = Table(show_header=True, header_style="bold cyan", box=None)
    for c in cols:
        table.add_column(str(c))
    for r in rows[:limit]:
        table.add_row(*[str(r[c]) for c in cols])
    console.print(table)
    if len(rows) > limit:
        console.print(f"[dim]… {len(rows) - limit} more row(s)[/dim]")


_HELP = """[bold]Commands[/bold]
  [cyan]\\help[/cyan]              this help
  [cyan]\\undo[/cyan]              revert the most recent write
  [cyan]\\revert <id>[/cyan]       revert a specific undo id
  [cyan]\\history[/cyan]           show this session's executed writes
  [cyan]\\tables[/cyan]            list tables the policy allows
  [cyan]\\quit[/cyan]              leave Human Mode

Type any SQL to run it through the safety layer. Risky writes show their
blast radius and ask before executing; allowed reads/writes just run."""


# --- the Human Mode loop -----------------------------------------------------


async def human_mode(sess: GuardedSession, policy: Policy) -> None:
    console.print(
        Panel(
            Text.from_markup(
                "[bold]Human Mode[/bold] — you write SQL, the safety layer has "
                "your back.\nEvery destructive write is simulated and shown "
                "before it runs. [dim]\\help for commands.[/dim]"
            ),
            border_style="cyan",
        )
    )
    history: list[tuple[str, str]] = []  # (action_id, sql) for executed writes

    while True:
        try:
            raw = Prompt.ask("[bold green]agentdb[/bold green] [dim]▸[/dim]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            return
        if not raw:
            continue

        # --- meta commands ---
        if raw in ("\\quit", "\\exit", "\\q"):
            console.print("[dim]bye[/dim]")
            return
        if raw in ("\\help", "\\h", "\\?"):
            console.print(_HELP)
            continue
        if raw == "\\tables":
            allowed = sorted(policy.allowed_tables or [])
            console.print(", ".join(allowed) if allowed else "[dim]all tables[/dim]")
            continue
        if raw == "\\history":
            if not history:
                console.print("[dim]no writes yet this session[/dim]")
            for aid, sql in history:
                tag = f"[cyan]{aid[:8]}[/cyan]" if aid else "[dim]—[/dim]"
                console.print(f"  {tag}  {sql}")
            continue
        if raw == "\\undo" or raw.startswith("\\revert"):
            await _do_revert(sess, history, raw)
            continue
        if raw.startswith("\\"):
            console.print(f"[red]unknown command[/red] {raw}  [dim](\\help)[/dim]")
            continue

        # --- SQL ---
        await _run_sql(sess, history, raw)


async def _run_sql(sess, history, sql) -> None:
    prop = await sess.propose(sql, actor="human")

    if prop.verdict == BLOCK:
        _render_block(prop)
        return
    if prop.verdict == CONFIRM:
        _render_confirm(prop)
        ans = Prompt.ask(
            "  Execute?", choices=["y", "n"], default="n", show_choices=True
        )
        if ans != "y":
            console.print("[dim]· cancelled[/dim]")
            return
        res = await sess.execute(prop, force=True)
    else:  # ALLOW / PASSTHROUGH
        res = await sess.execute(prop)

    _render_result(res)
    if res.executed and res.action_id:
        history.append((res.action_id, sql))


async def _do_revert(sess, history, raw) -> None:
    if raw.startswith("\\revert"):
        parts = raw.split()
        if len(parts) < 2:
            console.print("[red]usage:[/red] \\revert <undo id>")
            return
        action_id = parts[1]
    else:  # \undo -> most recent
        if not history:
            console.print("[dim]nothing to undo[/dim]")
            return
        action_id = history[-1][0]

    # Accept an 8-char prefix from the rendered output.
    full = next((a for a, _ in history if a.startswith(action_id)), action_id)
    try:
        result = await sess.revert(full, actor="human")
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    if result.ok:
        console.print(
            f"[green]✓ reverted[/green] [cyan]{full[:8]}[/cyan]"
            f"  ({result.rows_restored} row(s) restored)"
        )
        history[:] = [(a, s) for a, s in history if a != full]
    else:
        console.print(f"[red]revert failed:[/red] {result.error}")


# --- landing screen + wiring -------------------------------------------------


def _agent_mode_help() -> None:
    snippet = (
        '{\n'
        '  "mcpServers": {\n'
        '    "agentdb": {\n'
        '      "command": "python",\n'
        '      "args": ["-m", "adapters.mcp_server"]\n'
        '    }\n'
        '  }\n'
        '}'
    )
    console.print(
        Panel(
            Text.from_markup(
                "[bold]Agent Mode[/bold] — your AI agent writes the SQL; the "
                "safety layer guards it.\n\nPoint your MCP client (Claude Code, "
                "Cursor, …) at the server below, then drive it from the agent. "
                "Same engine, same guarantees as Human Mode.\n\n"
                f"[dim]{snippet}[/dim]"
            ),
            border_style="magenta",
            title="🤖 Agent Mode",
            title_align="left",
        )
    )


async def _build_session() -> tuple[GuardedSession, Policy, asyncpg.Pool, AuditLog]:
    policy = Policy.load(POLICY_PATH)
    pool = await asyncpg.create_pool(dsn=DB_DSN, min_size=1, max_size=4, timeout=5)
    async with pool.acquire() as c:
        unique_columns = await load_unique_columns(c)
    audit = AuditLog(AUDIT_LOG_PATH)
    await audit.start()
    store = UndoStore(policy.undo)
    sess = GuardedSession(
        pool, policy, undo_store=store, unique_columns=unique_columns, audit=audit
    )
    return sess, policy, pool, audit


async def _amain() -> None:
    console.print(
        Panel(
            Text.from_markup(
                "[bold cyan]agent-db-safety[/bold cyan]  "
                "[dim]runtime safety layer for SQL[/dim]\n\n"
                "Who is writing the SQL?\n"
                "  [bold]1[/bold]  🤖  An agent writes SQL  [dim](MCP)[/dim]\n"
                "  [bold]2[/bold]  ⌨   I write SQL          [dim](Human Mode)[/dim]"
            ),
            border_style="cyan",
            title="welcome",
            title_align="left",
        )
    )
    choice = Prompt.ask("Choose", choices=["1", "2"], default="2")
    if choice == "1":
        _agent_mode_help()
        return

    try:
        sess, policy, pool, audit = await _build_session()
    except (OSError, asyncpg.PostgresError) as exc:
        console.print(
            f"[bold red]Could not reach Postgres[/bold red] at {DB_DSN}\n{exc}\n"
            "[dim]Start the dev DB with `docker compose up -d`.[/dim]"
        )
        return
    try:
        await human_mode(sess, policy)
    finally:
        await audit.stop()
        await pool.close()


def main() -> None:
    """Console-script entry point (`agentdb`)."""
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
