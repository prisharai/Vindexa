# BENCHMARK_SPEC.md — Day 7 latency benchmark (the credibility doc)

> The authoritative spec for the Day 7 performance proof. The goal is a number a
> skeptical senior engineer would trust. If a choice here would flatter the
> result at the cost of honesty, don't make it. Cross-references CLAUDE.md §4.

---

## 0. The one question we are answering

> **How much latency does routing a query through the safety engine add, versus
> not having it — across a realistic workload, measured honestly?**

The headline deliverable is the **added overhead at p50 and p99** (in both
milliseconds and %), with the full distribution and environment disclosed. The
pass/fail gate is CLAUDE.md §4: **added p99 < 5 ms on the pass-through path.**

We are NOT measuring "how fast is Postgres." Postgres latency dominates and is
the same on both sides; the interesting quantity is the *delta* the engine adds.

## 1. What we compare (isolate exactly one variable)

Run the **same workload** against each of these, changing nothing but the layer
under test:

| Label | Path | What it isolates |
|---|---|---|
| **A — direct** | client → asyncpg → Postgres | theoretical floor; no engine at all |
| **B — pass-through** | client → engine (parse+classify+audit, no policy) → Postgres | cost of understanding + logging |
| **C — enforcing** | client → full engine (parse+classify+policy, observe/enforce) → Postgres | cost of the full safety logic |

The two headline numbers: **C minus A** (total overhead of the product) and
**C minus B** (overhead of the safety logic specifically). The §4 gate applies to
the pass-through path (B vs A) and to C where enforcement runs without
simulation.

Both A/B/C must use the **same machine, same Postgres instance, same connection
pool size, same dataset, same client harness.** The only difference is the layer.

## 2. Cardinal rule: open-loop load, no coordinated omission

- **Use an OPEN-LOOP load model.** Requests are issued on a fixed schedule at a
  target arrival rate (fixed-interval or Poisson). Do **not** use a closed loop
  that waits for each response before sending the next — that hides tail latency
  (coordinated omission).
- **Measure latency from the request's *scheduled* send time**, not its actual
  send time. `latency = response_received_at − intended_send_time`. If the
  harness itself falls behind schedule, that lateness MUST be counted in the
  latency, not discarded.
- **The load generator must not be the bottleneck.** Verify the harness has CPU
  headroom (ideally pin it to separate cores, or run it on a separate process);
  if the generator saturates, the run is invalid — you measured the harness.
- Record into an **HdrHistogram** (Python `hdrhistogram`) for accurate
  high-percentile capture. Do not compute percentiles from a naive sorted list of
  a small sample.

## 3. Realistic mixed workload (no single-query tricks)

- **Query mix, production-shaped:** ~80% reads, ~18% writes, ~2% "risky" writes
  that intentionally trigger the gated simulation path (Day 4). Reads must span
  shapes: point lookups, range scans, multi-table joins, and a few heavy
  aggregations — not all cheap, not all expensive.
- **Distribution, not uniform:** draw queries from the template set with a
  **Zipfian** (skewed) frequency — a few templates very common, a long tail of
  rare ones — so the **parse-cache hit rate mirrors reality** (target ~70–90%
  hits, and **report the measured hit rate**). Never send one identical query
  repeatedly (cache always hot = dishonest) and never send all-unique queries
  (cache always cold = also dishonest).
- **Bound parameters vary** (different ids/ranges) so you're not accidentally
  hitting only one cached Postgres plan or one buffer-cached page.
- Include the risky-write subset specifically to **prove the expensive
  simulation path stays contained** — i.e. that a small fraction of
  simulation-triggering writes does NOT poison the p99 of the whole workload.
  Report the workload's p99 with and without that subset.

## 4. Concurrency / rate sweep

Tail latency and overhead change under contention, so don't report a single
point. Sweep **target arrival rates** from light to near-saturation (e.g. a
geometric sweep of concurrency levels such as 1, 8, 32, 64, 128 in-flight, or
equivalent request rates), and report the table at each level. The most honest
headline is the overhead at a **realistic production load**, plus the point where
the system begins to saturate (knee of the curve).

## 5. Methodology (steady state, repeats, isolation)

- **Warmup then measure:** discard an initial warmup window (pool fills, caches
  warm, OS pages cache) before recording. Report the warmup length.
- **Enough samples for the tail:** ≥ 200,000 measured requests per cell so p99.9
  is meaningful (p99.9 needs ~10k+ samples minimum; more is better).
- **Repeat ≥ 5 independent runs** per configuration. Report the **median p99
  across runs and the spread** (min–max or stdev) — never a single cherry-picked
  best run.
- **Interleave A/B/C runs** (ABC, ABC, …) rather than all-A-then-all-B, so CPU
  thermal drift / background noise cancels out instead of biasing one layer.
- **Quiet the machine:** close other apps, disable turbo-throttling surprises
  where possible, note if on battery vs mains (Macs throttle on battery), and
  record load average. A noisy host invalidates tail numbers.

## 6. Two complementary views (report BOTH)

1. **Pure-overhead view — `SELECT 1`-class trivial queries.** Here DB time ≈ 0,
   so the A→C delta is almost entirely the engine's own cost. This is the
   honest *worst-case relative* overhead (the engine as a % of total is largest
   when the query is trivial). Report the absolute added ms.
2. **In-context view — the realistic mixed workload (§3).** Here the same
   absolute overhead is a small fraction of real query time. Report added ms AND
   added % of total.

Showing both pre-empts the two opposite accusations ("you hid the overhead
behind slow queries" vs "you only tested a toy query").

## 7. What to report (the artifact)

A `benchmarks/RESULTS.md` table, per concurrency level, per layer (A/B/C):

- p50, p90, p99, p99.9, max latency (ms)
- **added overhead vs A at p50 and p99** (ms and %)
- throughput (req/s) achieved vs target rate (to confirm no coordinated omission)
- measured parse-cache hit rate
- pass/fail vs the §4 5 ms p99 gate

Plus a disclosed **environment block**: CPU/cores, RAM, OS, on battery or mains,
Postgres version, pool size, dataset (Pagila + 3M/2M rows), load model, harness,
number of runs, warmup length. Reproducibility is what converts a number into
trust.

## 8. The CI gate

Add a CI check (CLAUDE.md §8 Day 7) that runs a **scaled-down but same-shape**
version of this benchmark and **fails the build if added p99 on the pass-through
path exceeds 5 ms.** The CI version can use fewer samples for speed, but must
keep the open-loop model and the realistic mix shape — a closed-loop CI check
would defeat the purpose.

## 9. Anti-patterns — do NOT do these (they make the number a lie)

- ❌ Closed-loop load generation (causes coordinated omission).
- ❌ Reporting mean/average latency as the headline.
- ❌ Firing one identical query (cache always hot) or all-unique queries (always
  cold).
- ❌ Percentiles from a tiny sample, or from a single run.
- ❌ Letting the load generator saturate (measuring the harness, not the system).
- ❌ Comparing across different machines, pool sizes, or datasets.
- ❌ Only testing trivial queries, or only testing slow queries.
- ❌ Quoting the in-process micro-benchmark (`evaluate()` in µs) as the overhead
  number. That is a component check, not the end-to-end proof.
- ❌ Discarding the harness's own scheduling lateness from the latency figure.

## 10. The honest framing for the writeup

A middleware checkpoint can never be *literally* zero overhead — it's always a
small positive number. The defensible, true claim is: "added p99 of **X ms** on a
realistic mixed workload at **Y req/s** — below human/system perceptibility — and
normal human traffic doesn't traverse this path at all." State the X, the Y, the
environment, and the method. Never claim "no slowdown" without this number behind
it.

_Last updated: pre-Day-7._
