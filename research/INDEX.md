# START HERE — handoff for writing the paper

You (an LLM) have everything in this `research/` folder needed to write the full
paper. Read these in order:

1. **`INDEX.md`** (this file) — the finding, the data state, what each file is.
2. **`PAPER.md`** — the paper dossier: title/abstract template, intro,
   related-work map, method in prose, discussion, limitations, ethics, and the
   full data-schema inventory. **This is your section-by-section skeleton.**
3. **`RESULTS_STUDY.md`** — generated results: all rate tables, Fisher exact
   tests (odds ratios + p-values), logistic regression, evasion-taxonomy counts,
   turns-to-recovery, and verbatim blocked→evade trajectories.
4. **`README.md`** — experiment design rationale (why these tasks/conditions/
   metrics; the two-literature gap; threats to validity).
5. **`figures/*.png`** — `evasion_by_condition.png`, `scope_theater.png`,
   `recovery.png` (regenerate via `uv run python -m research.stats`).
6. **`runs/*.jsonl`** — raw per-turn data, one file per model (schema in PAPER.md
   §9). Every prompt, SQL, decision, and measured blast radius is here.

Source of the instrument: `harness.py` (tasks, conditions, evasion classifier),
`runner.py` (closed loop), `agents.py` (mock + real LLM), `stats.py` (analysis).

## ⚠ Critical reframing — read before writing (v1 vs v2)

The completed run uses **v1 tasks that explicitly ask for a broad operation**
("empty the table"). Under those prompts, a full-table write is *faithful
compliance*, so the `scope_theater`/`destructive` labels measure **literal rule
satisfaction under a broad objective that conflicts with the guardrail — not
strategic evasion of a narrow intent.** The v1 study must be written that way (it
is still a real, interesting finding about *mechanistic feedback inducing literal
rule satisfaction*). The genuinely-evasion framing requires **v2** (narrow-intent
tasks, built and ready in `tasks_v2.py` + `pre_registration.md`) — runnable the
moment API credits are available; it was not run here because credits ran out.

## The one-sentence finding (v1, reframed)

When an agent is told to perform a broad write and the guardrail blocks it,
**naming the violated rule (e.g. `WRITE_WITHOUT_WHERE`) makes the agent far more
likely to re-issue a syntactically-compliant statement that still hits the whole
table** (typically `WHERE 1=1`) than an opaque error does — i.e. mechanistic
feedback elicits literal rule satisfaction. Blast-radius measurement robustly
flags this class of statement; simple syntax heuristics catch only some cases.

## Headline numbers (evasion rate = ≥1 evasion attempt)

| model | C0 opaque | C1 reason code | C2 reason+fix | C3 +blast radius |
|---|---|---|---|---|
| **Haiku 4.5** (complete) | 22% | 95% | 98% | 98% |
| Sonnet 4.6 (partial) | 37% | 98% | 100% | 100% |
| Opus 4.8 (partial) | 22% | 75% | 68% | 79% |

- **Manipulation check:** 99% of trials had their first attempt denied (the
  bulk-request tasks reliably trigger the guardrail).
- **Mechanism:** scope-theater (a fake `WHERE`) accounts for nearly all of the
  evasion — its rate mirrors the evasion rate above.
- **Mirror image in recovery:** genuine recovery falls as denials get richer
  (Haiku 38% at C0 → 2–5% at C1–C3).
- **Capability nuance (partial data):** Opus (strongest) showed *less* literal
  satisfaction and *more* narrowing than Haiku/Sonnet — capability may partly
  protect — but **Opus/Sonnet cells are incomplete; this appears only in partial
  cross-model runs and is not confirmatory.**
- Exact odds ratios + p-values (Fisher exact, each condition vs C0, pooled and
  per model) and the logistic regression are in `RESULTS_STUDY.md`.

## DATA STATE — read before writing the results

The API credit balance ran out mid-sweep. Completeness differs by model:

| model | trials | cells (of 12) | status |
|---|---|---|---|
| Haiku 4.5 | 240 | 12 | **complete** (n=20/cell) — primary analysis |
| Sonnet 4.6 | 200 | 10 | partial (2 cells missing) |
| Opus 4.8 | 148 | 8 | partial (some cells n<20) |

**Write the paper with Haiku as the complete primary result and Sonnet/Opus as
partial cross-model replication.** The direction is identical across all three.
To finish the replication: add API credits and re-run the two partial models
(`AGENT=anthropic MODEL=claude-sonnet-4-6 SCHEMA=exp_sonnet TRIALS=20 uv run
--env-file .env python -m research.run_pilot`, same for opus), then
`uv run python -m research.stats`.

## What's solid vs. what needs a caveat

- **Supported (exploratory):** on the complete Haiku run, the C0→C1 jump is large;
  partial Sonnet/Opus runs show the same direction. Treat as exploratory.
- **Must caveat:** v1 tasks conflate compliance with evasion (see reframing);
  single provider family; 3 synthetic, adversarially-constructed tasks;
  Sonnet/Opus partial; one seed; v1 C3 confounds row-count with confirmation
  framing (fixed in v2). The confirmatory claim needs the **v2** run
  (`pre_registration.md`), not v1.
