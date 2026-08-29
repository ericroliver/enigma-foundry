# SGLang Integration — Technical Implementation Plan

Status: **draft for operator approval** (2026-08-29). Supersedes nothing yet —
vLLM support stays; SGLang is added as a second engine.

Source research: `ws/enigma-cerebrus/docs/foundry-sglang-research.md`.

## 0. Summary

enigma-foundry currently assumes vLLM (`vllm serve <hf_id>`). SGLang is a
different launcher (`sglang serve --model-path <id>`) with different flags,
but the same OpenAI contract. The integration is contained: an engine
abstraction, ordered-arg support, a two-stage health check + generation
probe, auxiliary-model downloads, and digest-pinned image refs. Then two new
catalog entries (canary + performance target).

Target performance (per research): SGLang NVFP4 + DFlash2 on Qwen3.8-27B
~30–50 TPS code/structured reasoning vs the current vLLM FP8+MTP at ~15–19
TPS. Warrants the work.

## 1. Engine abstraction

### 1.1 `engines.yaml` (repo root, new)

Engine definitions are owned by code, not catalog entries:

```yaml
vllm:
  launcher: [vllm, serve]
  model_argument: positional        # hf_id appended as a positional arg
  readiness_path: /health
  contract_path: /v1/models
  reserved_args: [--host, --port, --served-model-name]

sglang:
  launcher: [sglang, serve]
  model_argument: --model-path      # flag form
  readiness_path: /health
  contract_path: /v1/models
  reserved_args: [--host, --port, --served-model-name, --model, --model-path]
```

`rendered_argv()` consults the engine record to place the model identifier.
Catalog validation rejects any `engine_args` hitting `reserved_args`.

### 1.2 Catalog schema change

- New required field: `engine: vllm|sglang` (default `vllm` for backward
  compat during migration; make required once all entries migrated).
- `vllm_args` renamed `engine_args`; `vllm_args` accepted as a deprecated
  alias (warn on use).

### 1.3 Ordered argument lists

`engine_args` may be a mapping (current style → `--key value`, bare flag for
true booleans) **or** an ordered YAML list of scalars appended verbatim:

```yaml
engine_args:
  - --context-length
  - "262144"
  - --speculative-draft-model-path
  - incoai/Qwen3.8-27B-DFlash2
```

Motivation: repeated flags, list-valued args, and quoting-sensitive JSON all
become expressible. Validation: list items must be str/int/float; mapping
form keeps existing bool handling.

## 2. Health checks — two stage + generation probe

Current: poll `GET /v1/models` until alias `enigma/default` appears.

New sequence in the swap worker (both engines):

1. **Readiness**: poll `GET <engine.readiness_path>` (both vLLM and SGLang
   expose `/health`) until HTTP 200, up to `health_timeout_secs`.
2. **Contract**: poll `GET /v1/models` until served alias `enigma/default`
   present in `.data[].id`.
3. **Generation probe**: one deterministic chat completion:

   ```
   POST /v1/chat/completions
   {"model": "enigma/default",
    "messages": [{"role":"user","content":"Reply with exactly: READY"}],
    "max_tokens": 16}
   ```

   Pass on HTTP 200 with any non-empty `choices[0].message.content`.
   Kubelets of weirdness (template, reasoning parser) surface here instead
   of on the first agent.

`current.json` only written after all three pass. Probe result recorded in
the swap journal. Failure of any stage → auto-rollback (unchanged logic).

Probe catalog knobs (optional, defaults apply):

```yaml
probe:
  prompt: "Reply with exactly: READY"
  max_tokens: 16
  timeout_secs: 60
```

## 3. Auxiliary downloads (draft models)

New optional catalog field:

```yaml
aux_models:
  - incoai/Qwen3.8-27B-DFlash2    # downloaded alongside primary
```

- `download` worker: `snapshot_download(hf_id)` for primary + each aux entry.
- `weights_cached()`: primary **and** all aux must be cached.
- Cache size estimate: `est_size_gb` should cover primary + aux.
- Journal records per-model verdicts.

## 4. Digest-pinned image refs

SGLang Qwen3.8 images are publish-mutable tags (`dev-qwen38-27b-dflash2`);
pin by digest. Image ref strings like
`lmsysorg/sglang@sha256:616a3e97...cafe` must work in:

- `image_present()`: try `docker image inspect <ref>`; for tagless digest
  refs, fall back to scanning `docker images --digests --format`. If neither
  confirms, tell the operator to `docker pull <ref>` (preflight fails
  cleanly, same as today).
- `cmd_list` cached/image column: same resolution.

Multi-platform digest (ARM64 resolved by the registry at pull):
`lmsysorg/sglang@sha256:616a3e97f45191af975896cfa644279096cb31bd408a071c2e99ca7209c3cafe`

## 5. New catalog entries

### 5.1 Canary — FP8 on SGLang (proves the engine plumbing cheaply)

`catalog/qwen3-8-27b-fp8-sglang.yaml` — same weights as the live vLLM entry
(29 GiB, already cached), on the pinned SGLang image. Minimal args:

```yaml
name: qwen3-8-27b-fp8-sglang
engine: sglang
hf_id: Qwen/Qwen3.8-27B-FP8
image: lmsysorg/sglang@sha256:616a3e97f45191af975896cfa644279096cb31bd408a071c2e99ca7209c3cafe
shm_size: 32g
est_size_gb: 30
health_timeout_secs: 900
engine_args:
  - --context-length
  - "65536"
  - --mem-fraction-static
  - "0.80"
  - --reasoning-parser
  - qwen3
  - --tool-call-parser
  - qwen3_coder
  - --sampling-defaults
  - model
```

### 5.2 Performance target — NVFP4 + DFlash2

`catalog/qwen3-8-27b-nvfp4-dflash2.yaml` per research recipe:

```yaml
name: qwen3-8-27b-nvfp4-dflash2
engine: sglang
hf_id: RadixArk/Qwen3.8-27B-NVFP4
image: lmsysorg/sglang@sha256:616a3e97f45191af975896cfa644279096cb31bd408a071c2e99ca7209c3cafe
shm_size: 32g
est_size_gb: 32
health_timeout_secs: 900
aux_models:
  - incoai/Qwen3.8-27B-DFlash2
engine_args:
  - --context-length
  - "262144"
  - --mem-fraction-static
  - "0.80"
  - --attention-backend
  - flashinfer
  - --chunked-prefill-size
  - "2048"
  - --mamba-ssm-dtype
  - bfloat16
  - --mamba-radix-cache-strategy
  - extra_buffer
  - --reasoning-parser
  - qwen3
  - --tool-call-parser
  - qwen3_coder
  - --sampling-defaults
  - model
  - --disable-prefill-cuda-graph
  - --speculative-algorithm
  - DFLASH
  - --speculative-draft-model-path
  - incoai/Qwen3.8-27B-DFlash2
  - --speculative-num-draft-tokens
  - "8"
```

Note: `--mem-fraction-static 0.80` intentionally — NVIDIA's Spark playbook
warns 0.85 trips earlyoom on unified memory; 0.80 served in every tested
config.

## 6. Implementation order (small, individually revertible commits)

1. Engine abstraction: `engines.yaml` + `engine` field + `engine_args`
   rename (alias `vllm_args`) + ordered-list support. Migrate the 4 existing
   catalog entries in the same commit (mapping form kept).
2. Two-stage health + generation probe (+ probe knobs).
3. `aux_models` in download worker + cached check.
4. Digest-pinned image resolution.
5. Canary catalog entry; download; operator-directed swap; smoke the four
   API paths (plain chat, streaming, reasoning, tool call).
6. NVFP4 + DFlash2 entry; download (aux included); swap; measure TPS.
7. If perf confirmed: keep vLLM entries as rollback targets until operator
   retires them.

## 7. Open-source prep (repo going public)

Repo currently at `/home/eo/enigma-dev/repos/enigma-foundry` (local git,
9 commits). Remote: `git@github.com:ericroliver/enigma-foundry.git`.

Before `public` visibility:

- [ ] LICENSE file (operator picks: MIT/Apache-2.0 suggested).
- [ ] README: scrub internal host/paths to env-var examples
      (`FOUNDRY_STATE_DIR` override already exists — good).
- [ ] `git remote add origin` + push (done as part of this change).
- [ ] PLAN.md (internal skill copy) stays internal; docs/ in repo only holds
      public-facing material. This SGLang plan is safe to publish.

## 8. Risks / non-goals

- Rollback semantics unchanged: same-container-name discipline survives.
- SGLang image `dev-qwen38-27b-dflash2` is a moving tag — hence digest pins.
  Re-vetting required on upgrades (record digest in commit message).
- `/v1/responses` compatibility (non-chat endpoint) deliberately out of
  scope; probe only covers chat completions. If Enigma ever needs
  `/v1/responses`, add a capability probe per research.
