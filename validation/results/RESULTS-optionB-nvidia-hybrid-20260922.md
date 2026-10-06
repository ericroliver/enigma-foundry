# Option B cutover result — qwen3-8-flash-next-nvidia-hybrid (2026-09-22)

Swap 20260922-151602 verdict=success (13 min boot). Prior live: qwen3-8-flash-next-nvfp4.
Baseline (2026-09-21, live agent load, RadixArk @209646c, EXACT_TOPK=1):
agg ~12 tok/s @2 streams, ~6 tok/s/stream, TTFT ~55s lifetime avg, KV 80-97% + queueing.

## New profile (NVIDIA @fc694b54 -fp8hybrid, det-topk, draft-vocab 65k, FAST_ROWS=0, thr 2048)
- tps-benchmark --concurrency 2: agg 2.4 tok/s, avg decode 15.2 tok/s, TTFT 9.2s (4/4 ok)
- tps-benchmark --concurrency 4: agg 3.4 tok/s, avg decode 12.4 tok/s, TTFT 10.5s (8/8 ok)
- Bench caveats: short prompts (~500 tok) and short outputs (7-15 tok) — decode/s is
  reliable; agg tok/s and TTFT are NOT comparable to the 100K+-context baseline yet.

## Verdict so far
- Per-stream decode ~2-2.5x baseline (15.2 vs ~6 @2 streams; 12.4 @4 streams).
- Host memory: healthy under load post-swap (40G avail during boot); swappiness=10 applied
  + persisted (/etc/sysctl.d/99-spark-inference.conf). Idle si/so = 0. 13G avail at rest
  with the model loaded — tight but no thrash.
- PLE metrics (PROM_MULTIPROC) flowing: 332 lookups, 1.98s gather over 61.7k rows.
- REAL verdict deferred: needs a day of live agent traffic vs 2026-09-21 metrics
  (TTFT lifetime avg, agg tok/s under concurrent real prefixes, queueing).
- Rollback: foundry swap qwen3-8-flash-next-nvfp4 (weights+image cached).
- Pending: PREWARM back to 1 on a quiet boot to validate page-cache warm path.
