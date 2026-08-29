---
name: enigma-foundry
description: List, swap, download, and remove vLLM models on a host via enigma-foundry. Handles pre-download, preflight validation, detached swap with auto-rollback, and post-swap verification.
---

# enigma-foundry — model server manager

**enigma-foundry** manages a dockerized vLLM server on a host (one model at a
time). Run the CLI directly on the target host, or over SSH:

```bash
~/enigma-foundry/bin/enigma-foundry <subcommand>          # local
ssh <host> '~/enigma-foundry/bin/enigma-foundry ...'      # remote
```

## The contract that keeps agents alive

Every model is served as container `vllm-server` on **port 8000** with the
served alias **`enigma/default`**. These are global constants — catalog
entries cannot override them. Agents always talk to `http://<host>:8000`
model `enigma/default`; a swap changes the weights, never the address.

## Subcommands

| Command | Effect |
|---|---|
| `list` | Catalog entries with cached / image-present / LIVE markers |
| `status` | Server health, live model, swap/download progress, last swap verdict. Read-only — always safe |
| `download <name>` | Pre-download weights in a detached worker; server keeps running |
| `swap <name>` | Swap the live model. Requires cached weights unless `--allow-download`. `--force` re-swaps the live model (config-only edits) |
| `remove <name>` | Full removal: catalog YAML + HF weights + docker image. **Refuses if live** |
| `logs [id]` | Worker log (default: most recent). `--container` for docker logs |

## Swapping — the sever-and-reconnect pattern

`swap` runs full preflight (YAML valid, image present, weights cached or
`--allow-download` + disk space, target ≠ live unless `--force`, no other
swap in progress) and **fails before touching the running container** if
anything is wrong. On success it dispatches a detached worker and returns
immediately with a swap id. **The server goes down seconds later.**

If you are an agent served by this model, your connection dies mid-turn.
That is expected:

1. Fire `swap` (or have the operator fire it). Note the swap id.
2. Your session breaks. Nothing more to do — the worker is autonomous.
3. On your next invocation, run `status`:
   - `server: HEALTHY` + `live model: <target>` → swap succeeded, continue.
   - `last swap: ... verdict=rolled-back` → target failed its health check;
     foundry restored the previous model. Read `logs <swap-id>` for why.
   - `verdict=failed` → swap AND rollback failed; server is down. Escalate
     to the operator — do NOT retry blindly.
   - `swap: IN PROGRESS` → still working (model load can take many minutes;
     with `--allow-download`, up to hours). Wait and re-poll.

The worker auto-rolls back to the previous catalog entry on any failure
(container exit, `docker run` error, health timeout) — the server is never
intentionally left dead.

## Standard flows

**Swap between cached models:** `list` → `swap <name>` → reconnect → `status`.

**Add a new model:**
1. Author `catalog/<name>.yaml` (schema in README; `name` must match
   filename; `entrypoint: serve-prefix` for entrypoint-suppressed images,
   `bare` when the image entrypoint is already the server; realistic
   `est_size_gb`).
2. `docker pull` the image if new.
3. `download <name>` (detached; poll `status`).
4. `swap <name>`.

**Remove a model:** `remove <name>` — refuses if live; swap away first.

## Cautions

- `status`/`list` are safe read-only commands. **Never `swap` without
  operator approval** — a swap kills the live server for minutes.
- If the server was started outside foundry (e.g. a legacy run script),
  `status` still re-detects the live model from the container; foundry
  manages it from there on.
- State and logs live outside the repo at `~/enigma-foundry-state`
  (override with `FOUNDRY_STATE_DIR`).
