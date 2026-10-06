# Option B — vLLM Flash-Next update for interactive agents (operator-supplied research)

Status: **saved as an option 2026-09-21** (operator research, partially verified on spark).
Alternative/complement to Option A (SGLang cutover — prep complete: image pulled,
NVFP4+DFlash2 weights cached). Neither executed without operator downtime approval.

## Thesis

The DGX Spark is not fundamentally too slow. Current config optimizes for max
context + correctness, poor fit for 3 interactive agents. Two biggest problems:

1. `VLLM_QSA_EXACT_TOPK=1` — exact `torch.topk` costs ~20–40% on long prefills.
   Project uses deterministic kernel instead: `DET_TOPK=1`, `EXACT_TOPK=0`.
2. Older recipe/checkpoint. Project now recommends NVIDIA checkpoint with
   hybrid side layers: ~22 tok/s/agent measured (4 agents) vs our ~6 tok/s,
   +15–22% more KV capacity than RadixArk at comparable settings.

## Proposed run (new upstream, after image rebuild)

```bash
git pull
MODEL=nvidia/Qwen3.8-Flash-Next-NVFP4 ./flash setup default

# three interactive agents:
MODEL=nvidia/Qwen3.8-Flash-Next-NVFP4 MODE=hybrid MTP=2 SEQS=4 \
GPU_MEM=0.80 PREWARM=1 FAST_ROWS=0 WORKERS=32 PROM_MULTIPROC=1 \
EXTRA='--long-prefill-token-threshold 2048' \
./flash serve default
```

New flags vs our setup: `VLLM_QSA_EXACT_TOPK=0`, `DET_TOPK=1`, `DRAFT_VOCAB=1`,
`FAST_ROWS=0` (thread-pool gathers, ~+17% four-stream aggregate decode),
reduced draft vocabulary (~+20% decode).

## KV guidance — do NOT allocate the 33.7 GiB

vLLM's log suggestion is mathematically available memory, not a good Spark config:
KV, weights, page cache, and the mmap'd PLE table compete in the same unified pool.
Extra explicit KV can evict PLE page cache / push host to swap → every step faults
NVMe. NVIDIA hybrid checkpoint should grow the KV pool without squeezing page cache.

**Verified on spark 2026-09-21:** host IS swapping under load — swpd 5.93 GB,
nonzero si/so during inference, swappiness=60 (default). Confirms the concern.
Recommended: `sudo sysctl -w vm.swappiness=10` + watch `vmstat 1` (si/so/wa).

## `--long-prefill-token-threshold` (fairness control)

| Value | Result |
|---|---|
| None/8192 | Best isolated TTFT, decodes can stall 4–7 s/token |
| **2048** | Reasonable 3-agent compromise (start here for Enigma) |
| 1024 | More responsive active agents, slower TTFT |
| 512 | Max fairness, big prefill penalty |

Project measured: two concurrent 72K prefills — 66/110 s default, 89/89 s @2048, 98/98 s @1024.

## Context policy (agent-side, more important than --max-model-len)

Paged KV means lowering max-model-len reclaims nothing retroactively, but stops
one agent monopolizing the pool:
- Summarize/compact ~48–64K
- Hard cap routine sessions 96K
- 262K only for explicit long-context jobs
- Avoid dumping huge tool output into context
- Keep system/tool defs byte-identical (our 83.4% prefix hit is excellent;
  residual TTFT ≈ large uncached tails + concurrent prefill scheduling)

## Expected result

- Aggregate decode materially above 12 tok/s
- ~12–22 tok/s/stream for 3 agents (context-dependent)
- Less KV pressure, smoother when one agent does a large prefill
- TTFT stays high for genuine 100K+ cold prompts — single Spark cannot fix that

## Verification facts collected on spark (2026-09-21)

- Forge source `~/forge/qwen38-flash-next/source` pinned at 209646c; **upstream
  origin/main is 52 commits ahead** (fetched, not merged) — includes: NVIDIA
  checkpoint default (#17), FAST_ROWS default-0, prepare-hybrid snapshot repair,
  v0.29 Dockerfile backport, parser fixes, EFFORT_ALIAS, PLE timing stats.
- `flash` CLI does not exist at 209646c → image rebuild required for new flow.
- No `nvidia/Qwen3.8-Flash-Next-NVFP4` in HF cache yet (~122 GiB; disk has 3.0T free).
- SGLang prep state: image pulled, NVFP4+DFlash2 weights cached (finished 20260921-122207).
