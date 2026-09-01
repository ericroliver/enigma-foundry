# enigma-foundry

Manage a fleet of dockerized **vLLM** or **SGLang** model servers behind
one stable endpoint. One live model at a time, a validated YAML catalog of
candidate models, detached swap workers with **auto-rollback**, and a state
journal — built so that LLM-serving agents can survive (or even perform)
their own model swaps.

The agent-survival contract: every catalog entry is served as container
`vllm-server` on port **8000** with served alias **`enigma/default`**.
Clients hardcode that address; a swap only changes the weights behind it.

## Requirements

- Linux host with **docker** + GPU runtime (`--gpus all`)
- **python3** + **pyyaml** (the CLI is python3 + pyyaml only)
- HF weights cached under `~/.cache/huggingface` (or downloaded via
  `download`)

## Install

```bash
git clone https://github.com/ericroliver/enigma-foundry.git
cd enigma-foundry
python3 -c 'import yaml'        # verify pyyaml
```

State and logs live outside the repo (default `~/enigma-foundry-state`, or
set `FOUNDRY_STATE_DIR=/path`) so code redeploys never clobber runtime
state.

## Usage

```
bin/enigma-foundry list                     # catalog + cached/image/live markers
bin/enigma-foundry status                   # server health, live model, swap progress
bin/enigma-foundry download <name>          # pre-fetch weights (server keeps running)
bin/enigma-foundry swap <name>              # swap live model (detached worker)
bin/enigma-foundry swap <name> --force      # re-swap live model (config-only edits)
bin/enigma-foundry swap <name> --allow-download   # swap even if weights uncached
bin/enigma-foundry remove <name>            # catalog yaml + weights + image (refuses if live)
bin/enigma-foundry logs [id]                # worker log (default: most recent)
bin/enigma-foundry logs --container         # docker logs of vllm-server
```

## Swap behavior

`swap` validates everything first (YAML, image present, weights cached or
`--allow-download` + disk space, target not already live unless `--force`,
no swap in progress), then dispatches a **detached worker** (`setsid`) and
returns immediately. The caller's session can die — the worker finishes
alone:

1. `docker rm -f vllm-server`
2. `docker run` rendered from the catalog YAML + global constants
3. three-stage health gate (per-model `health_timeout_secs`, default 900s;
   with `--allow-download` the clock resets on download progress, hard cap 6h):
   1. **readiness** — `GET <engine readiness_path>` (`/health`) → HTTP 200
   2. **contract** — `GET /v1/models` lists the served alias
   3. **generation probe** — one deterministic chat completion must return
      non-empty content (template/parser breakage surfaces here, not on the
      first agent request)
4. success → `state/current.json` + journal verdict `success`
5. failure → **auto-rollback** to the previous catalog entry, verdict
   `rolled-back` | `failed` — the server is never left dead if a rollback
   target exists.

Journals: `state/swap-<id>.json` (events, verdict, log tail on failure).

## Authoring a catalog entry

```yaml
name: my-model              # must match filename catalog/my-model.yaml
engine: vllm                # engine record from engines.yaml (vllm | sglang);
                            # owns launcher, model placement, health paths
hf_id: Org/Model-Name       # HF repo id
image: nvcr.io/nvidia/vllm:26.06-py3   # digest pins (repo@sha256:...) supported
entrypoint: serve-prefix    # serve-prefix = prepend the engine launcher
                            # bare = image's own entrypoint is the server (vllm only)
shm_size: 32g
est_size_gb: 81             # for download disk-space preflight (cover primary+aux)
health_timeout_secs: 900
aux_models:                 # optional: extra HF repos downloaded alongside
  - Org/Draft-Model         # (e.g. speculative-decoding draft heads)
engine_args:                # mapping form: --key value; true = bare flag
  max-model-len: 65536
  enable-prefix-caching: true
probe:                      # optional generation-probe overrides
  prompt: "Reply with exactly: READY"
  max_tokens: 16
  timeout_secs: 60
notes: free text
```

`engine_args` also accepts an **ordered list** of scalars appended verbatim
(repeated flags, quoting-sensitive values):

```yaml
engine_args:
  - --context-length
  - "262144"
  - --mem-fraction-static
  - "0.80"
```

Optional fields for runtime environments and mounts:

```yaml
environment:                # rendered as -e KEY=VALUE; quote values like "1"
  HF_HUB_OFFLINE: "1"
  VLLM_PLE_MMAP: "1"
mounts:                     # extra -v mounts; read_only: true adds :ro
  - source: /srv/forge/qwen38/runtime-cache
    target: /root/.cache
    read_only: false
drop_cache_below_gb: 60     # optional: swap worker syncs and drops page caches
                            # (sudo -n) when MemAvailable is below this threshold,
                            # between old-container exit and candidate start
```

The engine's `reserved_args` (`--host`, `--port`, `--served-model-name`, and
model placement for SGLang) are rejected in `engine_args` — they are global
constants. `vllm_args` is still accepted as a deprecated alias for
`engine_args`. Preferred flow for a new model: add YAML → `docker pull`
the image → `download <name>` → `swap <name>`.

## Agent-facing skill

`skill/SKILL.md` is a generic, deployable [goose](https://github.com/block/goose)
skill describing the CLI and the sever-and-reconnect swap pattern. Drop it
into your agent's skills directory and its model calls keep working across
swaps.

## Layout

```
catalog/<name>.yaml     one file per model (validated on every run)
engines.yaml            engine records: launcher, model placement, health paths
bin/enigma-foundry      the CLI (python3 + pyyaml only)
skill/SKILL.md          generic agent skill for the CLI
docs/                   design & integration plans
<$FOUNDRY_STATE_DIR | ~/enigma-foundry-state>/
  state/                current.json, swap journals, locks (gitignored)
  logs/                 worker + download logs
```

## Roadmap

- `remove` and rollback path still need live-fire testing (rollback only
  triggered on synthetic failures so far).

## License

MIT — see `LICENSE`.
