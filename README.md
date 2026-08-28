# enigma-foundry

vLLM model server manager for the **spark** host (DGX Spark, GB10).

One stable endpoint — container `vllm-server`, port **8000**, served alias
**`enigma/default`** — backed by a swappable catalog of models. The stable
alias/port is the agent-survival contract: agents talk to `enigma/default`
and never care which weights are behind it.

## Layout

```
catalog/<name>.yaml     one file per model (validated on every run)
bin/enigma-foundry      the CLI (python3 + pyyaml only)
state/                  current.json, swap journals, locks (gitignored)
logs/                   worker + download logs (gitignored)
```

## Usage

```
bin/enigma-foundry list                     # catalog + cached/image/live markers
bin/enigma-foundry status                   # server health, live model, swap progress
bin/enigma-foundry download <name>          # pre-fetch weights (server keeps running)
bin/enigma-foundry swap <name>              # swap live model (detached worker)
bin/enigma-foundry swap <name> --allow-download   # swap even if weights uncached
bin/enigma-foundry remove <name>            # catalog yaml + weights + image (refuses if live)
bin/enigma-foundry logs [id]                # worker log (default: most recent)
bin/enigma-foundry logs --container         # docker logs of vllm-server
```

## Swap behavior

`swap` validates everything first (YAML, image present, weights cached or
`--allow-download` + disk space, not already live, no swap in progress),
then dispatches a **detached worker** (`setsid`) and returns immediately.
The caller's SSH session can die — the worker finishes alone:

1. `docker rm -f vllm-server`
2. `docker run` rendered from the catalog YAML + global constants
3. health-poll `GET /v1/models` for HTTP 200 + `enigma/default`
   (per-model `health_timeout_secs`, default 900s; with `--allow-download`
   the clock resets on download progress, hard cap 6h)
4. success → `state/current.json` + journal verdict `success`
5. failure → **auto-rollback** to the previous catalog entry, verdict
   `rolled-back` | `failed` — the server is never left dead if a rollback
   target exists.

Journals: `state/swap-<id>.json` (events, verdict, vLLM log tail on failure).

## Authoring a catalog entry

```yaml
name: my-model              # must match filename my-model.yaml
hf_id: Org/Model-Name       # HF repo id
image: nvcr.io/nvidia/vllm:26.06-py3
entrypoint: serve-prefix    # serve-prefix = prepend `vllm serve`; bare = image is the server
shm_size: 32g
est_size_gb: 81             # for download disk-space preflight
health_timeout_secs: 900
vllm_args:                  # rendered verbatim as --key value; true = bare flag
  max-model-len: 65536
  enable-prefix-caching: true
notes: free text
```

`host`, `port`, `served-model-name` in `vllm_args` are rejected — they are
global constants. Preferred flow for a new model: add YAML → `docker pull`
the image → `download <name>` → `swap <name>`.

## Ops

Canonical git repo (local only): `/home/eo/enigma-dev/repos/enigma-foundry`
on the cerebrus host. Deploy (code-only; state/logs live in `~/enigma-foundry-state` on
spark, outside the synced tree): `rsync -a --delete --exclude .git --exclude __pycache__
--exclude state/ --exclude logs/ repos/enigma-foundry/ spark:~/enigma-foundry/`

Legacy `~/run-vllm-*.sh` scripts on spark remain as manual fallback until
the operator removes them.
