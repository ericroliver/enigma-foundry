# SGLang Cutover Runbook — spark (GB10)

Status: **parked 2026-09-22** — operator chose Option B (vLLM flash-next update)
first; SGLang stays fully prepped as candidate #2. See
`docs/flash-next-interactive-tuning-research.md` and the Option B runbook below.
Original prep state (unchanged):
Companion to `docs/sglang-integration-plan.md` (design) and the enigma-foundry
skill (swap semantics). All swaps run through `enigma-foundry` — never raw docker.

## The contract (unchanged)

Clients always talk to `http://spark:8000`, model `enigma/default`. A swap
changes only the weights behind it. If an agent is mid-turn during a swap its
connection dies; the swap worker is autonomous and rolls back on failure.

## Baseline — what we're trying to beat (2026-09-21, live)

Live entry: `qwen3-8-flash-next-nvfp4` (forge/qwen38-flash-next:209646c, vLLM
patched, PLE mmap). Sustained agent load, measured over a 60s window and from
lifetime metrics (3.8k+ requests):

| metric | value |
|---|---|
| aggregate generation | ~12 tok/s (2 concurrent streams) |
| per-stream decode | ~6 tok/s |
| TTFT (lifetime avg) | ~55 s ⚠️ primary pain point |
| e2e latency (avg) | ~138 s |
| queue | 2 running / 1 waiting (capacity), KV cache 80–97% |
| prefix cache hit | 83.4% |
| MTP mean acceptance length | 3.0 (spec decode working) |

Config notes: max_model_len 262144, max_num_seqs 4, gpu_mem_util 0.80,
max_num_batched_tokens 8192 (chunked prefill). KV cache only 14.6 GiB at 0.80
util — vLLM log suggests `--kv-cache-memory=36180286976` (33.7 GiB) would fully
utilize; that is a vLLM-side tuning option if SGLang does not win.

Research expectation (see foundry-sglang-research): SGLang NVFP4 + DFlash2 on
Qwen3.8-27B ≈ 30–50 TPS code/structured reasoning vs ~15–19 TPS for vLLM FP8+MTP.

## Pre-downtime prep (DONE 2026-09-21 — no server impact)

- [x] SGLang image pulled on spark (digest-pinned): `lmsysorg/sglang@sha256:616a3e97...cafe`
- [x] Canary weights cached: Qwen/Qwen3.8-27B-FP8 (29 GiB)
- [x] Target weights download dispatched via foundry (NVFP4 + DFlash2 aux):
      download id `20260921-122207` — VERIFY COMPLETION before swap:
      `ssh spark '~/enigma-foundry/bin/enigma-foundry status'` → download: none in progress
      then `list` shows qwen3-8-27b-nvfp4-dflash2 CACHED=yes
- [x] Repo artifacts committed + deployed (this runbook, tps-benchmark.py)

## Downtime sequence (operator gives the word)

1. **Final baseline capture** (optional but recommended, ~5 min):
   `python3 ~/enigma-foundry/validation/tps-benchmark.py --concurrency 2 --requests 4`
   (save SUMMARY line into validation/results/)
2. **Canary swap** (engine plumbing, ~10–15 min): foundry swap qwen3-8-27b-fp8-sglang
   → poll `status` until HEALTHY + live model = canary
3. **Smoke the four API paths** against :8000 (plain chat / streaming /
   reasoning / tool call) — must pass before proceeding
4. **Quality gate**: `cd ~/enigma-foundry/validation && python3 ladder.py --rungs 64000 --positions 50,90,none`
5. **Target swap** (~10–15 min): foundry swap qwen3-8-27b-nvfp4-dflash2 → poll status
6. **Benchmark the target**:
   `python3 validation/tps-benchmark.py --concurrency 2 --requests 6` and again
   `--concurrency 4 --requests 8` (save both SUMMARY lines to results/)
7. **Record results**: commit SUMMARY lines + verdict into
   `validation/results/RESULTS-sglang-qwen3-8-27b-nvfp4-dflash2.md`

## Rollback

- Auto: swap worker rolls back to previous entry on failed health gate — nothing to do.
- Manual (perft/quality fail): `foundry swap qwen3-8-flash-next-nvfp4`
  (weights + forge image cached; load 8–13 min, gate 20 min).
- If swap AND rollback fail (server down): escalate to operator, do not retry blind.

## Decision criteria (operator)

- Keep SGLang target if: agg tok/s ≥ 2× baseline at concurrency 2, TTFT materially
  down, ladder 64K rung 100%.
- Keep vLLM Flash-Next if SGLang target can't hold the 262k-context agent profile
  or quality gate fails; consider the vLLM `--kv-cache-memory` tuning as plan B.
