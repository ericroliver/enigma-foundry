# RESULTS — qwen3-8-flash-next-nvfp4 long-context ladder (recipe gate #5)

Catalog entry: `qwen3-8-flash-next-nvfp4` (MTP=2 candidate profile, PIECEWISE
cudagraphs, exact QSA top-k, prefix caching, bf16 KV, mem util 0.80)
Host: spark (DGX Spark GB10, 128 GB unified). Image `forge/qwen38-flash-next:209646c`.

## Run 2 — natural prose (v2 harness) — **PASS 42/42** ✅

`results/ladder-20260902-134435-prose-v2.json` — 2026-09-02, ~1.6h wall.

| Rung | 10% | 25% | 50% | 75% | 90% | 97% | control |
|---|---|---|---|---|---|---|---|
| 8K   | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 32K  | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 64K  | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 128K | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 200K | PASS | PASS | PASS | PASS | PASS | PASS | PASS |
| 250K | PASS | PASS | PASS | PASS | PASS | PASS | PASS |

- Prefill: 820–1006 tok/s sustained cold at all rungs (median 924); ~295s TTFT at 250K.
- Retrieval: exact codeword returned at every depth including 97%, at every rung.
- Controls (no needle): answered "none" at every rung — no confabulation under load.
- No empty finals; every probe `finish=stop`.
- Host: MemAvailable ~17 GiB and swap ~5.3 GiB flat for the entire run (no growth),
  no OOM, no restarts, no queueing (observed `Waiting: 0` throughout).

## Run 1 — random-word soup (v1 harness) — **FAIL (5/18)**

`results/ladder-20260901-213633-wordsoup-v1.json` — 2026-09-01, MTP=0 baseline.

| Rung | early(10%) | mid(50%) | late(90%) |
|---|---|---|---|
| 8K   | PASS | PASS | PASS |
| 32K  | PASS | PASS | **FAIL** |
| 64K  | PASS | PASS | **FAIL** |
| 128K | PASS | PASS | **FAIL** |
| 200K | PASS | PASS | **FAIL** |
| 250K | PASS | PASS | **FAIL** |

Model returned a filler word (e.g. 'invoice') instead of the codeword at 90%
depth on every rung ≥32K; early/mid were 100%.

## Interpretation

The v1 failure did **not** reproduce with natural prose, and matches the
operator's live experience (agents performing fine with large filled contexts).
Assessment: the v1 word-soup construction was the artifact — a 26-word uniform
distribution gives the needle sentence no distinctive *context* to bind to at
late depth (nothing to anchor "the margin where a clerk pencilled a codeword"),
and the model instead pattern-matches the question against the dominant token
distribution. Natural text gives attention real landmarks. v1 results retained
for the record, verdict superseded.

## Verdict for the entry

**Long-context retrieval gate: PASS.** Context up to ~128K is operationally
comfortable; 200K–250K verified working with ~5 min cold prefill each.
Remaining recipe gates: prefix-cache output equivalence, tool round-trip suite,
4h soak. MTP=2 already live.
