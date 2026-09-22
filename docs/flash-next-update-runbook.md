# Option B Runbook — flash-next update (NVIDIA hybrid) via foundry

Status: **in progress 2026-09-22** (operator approved; agents idle). Contract kept:
container `vllm-server`, :8000, alias `enigma/default`. Companion to
`docs/flash-next-interactive-tuning-research.md`.

## Progress

- [x] Forge source pulled 209646c → 5be6637 (52 commits: flash CLI, det-topk kernel,
      NVIDIA checkpoint default, FAST_ROWS, draft vocab, EFFORT_ALIAS, v0.29 backport)
- [x] `./flash setup default` running detached (image build + nvidia weights ~122G +
      prepare-hybrid) — log: ~/.local/state/qwen38-flash/setup-20260922.log
- [x] foundry patched: `model_arg_override` (serve the -fp8hybrid snapshot) +
      `docker_flags` (tmpfs for PROM_MULTIPROC); deployed to spark
- [ ] Verify setup finished (image built, `.prepared` marker, hybrid snapshot complete)
- [ ] New catalog entry `qwen3-8-flash-next-nvidia-hybrid` (below)
- [ ] OPERATOR (before swap, needs sudo): `sudo sysctl -w vm.swappiness=10`
      (+ persist: `echo vm.swappiness=10 | sudo tee /etc/sysctl.d/99-swappiness.conf`)
- [ ] Swap via foundry → smoke 4 paths → tps-benchmark vs 2026-09-21 baseline
      (agg ~12 tok/s, ~6/stream, TTFT ~55s) → vmstat si/so/wa check
- [ ] Record + commit results

## Catalog entry (written after setup completes, exact hybrid snapshot path filled in)

name: qwen3-8-flash-next-nvidia-hybrid
engine: vllm
hf_id: nvidia/Qwen3.8-Flash-Next-NVFP4
model_arg_override: <snapshots/<rev>-fp8hybrid local path>
image: qwen38-flash-dgx
entrypoint: bare
shm_size: 16g
est_size_gb: 130
health_timeout_secs: 1200
docker_flags: [--tmpfs, /tmp/vllm-prometheus:rw,size=256m]
environment:
  HF_HUB_OFFLINE: "1"
  VLLM_PLE_MMAP: "1"
  VLLM_PLE_MMAP_WORKERS: "32"
  VLLM_PLE_MMAP_PREWARM: "1"
  VLLM_PLE_MMAP_MADVISE: "random"
  VLLM_PLE_MMAP_FAST_ROWS: "0"
  VLLM_QSA_EXACT_TOPK: "0"          # research: exact topk = -20-40% long prefill
  VLLM_QSA_DET_TOPK: "1"            # deterministic kernel (same output, faster)
  VLLM_QSA_DET_LIB: /opt/llm/kernel-det/_C_det.so
  VLLM_MTP_DRAFT_VOCAB: /opt/llm/draft_vocab_65536.npy
  VLLM_FP8_HYBRID: "1"
  VLLM_USE_DEEP_GEMM: "0"
  VLLM_FP8_PAD_M4: "0"
  VLLM_USE_FLASHINFER_SAMPLER: "1"
  VLLM_ALLOW_LONG_MAX_MODEL_LEN: "1"
  PROMETHEUS_MULTIPROC_DIR: /tmp/vllm-prometheus
mounts: (runtime-cache mirrors, fresh dirs per entry)
engine_args:
  - --max-model-len
  - "262144"                        # contract ctx; agent-side policy caps usage
  - --max-num-seqs
  - "4"
  - --gpu-memory-utilization
  - "0.80"
  - --enable-prefix-caching
  - --enable-chunked-prefill
  - --max-num-batched-tokens
  - "8192"
  - --long-prefill-token-threshold
  - "2048"
  - -cc.cudagraph_mode=PIECEWISE
  - '-cc.splitting_ops=<same PLE splitting list as the live entry>'
  - --no-enable-flashinfer-autotune
  - --kv-cache-dtype
  - auto
  - --enable-auto-tool-choice
  - --tool-call-parser
  - qwen3_coder
  - --reasoning-parser
  - qwen3
  - --speculative-config
  - '{"method":"mtp","num_speculative_tokens":2}'

Notes:
- serve.sh sets tool parser qwen3_coder for this checkpoint (live entry used qwen3_xml);
  serve.sh values win — smoke test must cover tool calls.
- Rollback target: qwen3-8-flash-next-nvfp4 (weights + forge image cached).
- Do NOT set --kv-cache-memory (unified-pool trade, see research doc).
- YARN: not used — 262144 is native; flash default profile uses 500k/YaRN but we
  keep the contract ctx and revisit after agent context policy lands.
