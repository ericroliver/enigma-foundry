# validation/

Model validation harnesses for foundry catalog entries. Run **before** promoting
a model to wider use; results live in `results/` (committed) as the formal record.

## ladder.py — long-context retrieval ladder

Recipe gate #5 for Qwen3.8-Flash-Next (see the Forge recipe): never jump straight
to max context. Probes rungs 8K → 250K with a needle sentence planted in natural
prose at several depths, plus a no-needle control per rung (distinguishes
"missed the needle" from "confabulates under pressure").

```bash
# on the serving host (spark), after deploying the repo:
cd ~/enigma-foundry/validation
python3 ladder.py                      # full ladder, ~1h; results → ~/validation-results/
python3 ladder.py --rungs 64000 --positions 50,90,none   # spot check
```

- Corpus: Project Gutenberg texts (Moby Dick, Pride & Prejudice, Sherlock
  Holmes, Tale of Two Cities), downloaded once to `corpus/` (gitignored),
  concatenated — heterogeneous text, like real agent contexts.
- Every probe uses a distinct seeded corpus offset so prefix caching cannot
  warm later prefills artificially.
- Prompts are non-thinking (`enable_thinking: false`) — retrieval failures
  cannot hide behind reasoning.
- Per rung captures host MemAvailable/swap before-after (ssh to `spark`).
- Exit code 0 = all probes passed.

Acceptance (recipe): 64K deterministic needle set at 100%; retrieval accuracy
recorded at every rung; watch for empty-final-content and swap growth.

## Results log

| Date | Model entry | Data | Verdict | File |
|---|---|---|---|---|
| 2026-09-01 | qwen3-8-flash-next-nvfp4 (MTP=0) | random-word soup, positions early/mid/late(90%) | **FAIL** — all late misses 32K+, early/mid 100% | results/ladder-20260901-213633-wordsoup-v1.json |
