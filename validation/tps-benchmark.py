#!/usr/bin/env python3
"""TPS benchmark — agent-shaped throughput measurement (read-only vs server).

Measures what actually matters for agent serving on GB10: per-request decode
tok/s, aggregate tok/s, TTFT and TPOT under concurrency, with a long shared
system prefix (agent traffic = repeated prefixes -> prefix-cache sensitive).
Works against any OpenAI-compatible endpoint (vLLM or SGLang).

Usage (on the serving host or any host that can reach it):
  python3 tps-benchmark.py                                    # 2 concurrent
  python3 tps-benchmark.py --concurrency 4 --requests 8
  python3 tps-benchmark.py --base http://spark:8000 --prompt-tokens 8000
  python3 tps-benchmark.py --no-prefix                        # cold-prefix mode

Exit code 0 if all requests completed, 1 otherwise. Results printed as a
table + one-line SUMMARY (grep-able for validation/results/ records).
"""
import argparse
import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SHARED_PREFIX = (
    "You are Cerebrus, the operations brain of an agentic system. You manage "
    "docker hosts, model servers, and validation harnesses across a lab. "
    "You are precise, conservative with changes, and you report before acting. "
    "You track tasks, verify state, and never invent numbers.\n"
    * 40  # ~3-4K tokens of shared prefix; scale with --prefix-repeats
)

def build_prompt(prefix_tokens_target: int, marker: int) -> str:
    """Shared prefix + a small unique tail so requests differ."""
    repeats = max(1, prefix_tokens_target // 45)
    base = ("You are a precise operations assistant. You manage docker hosts, "
            "model servers, and validation harnesses. You report before acting.\n")
    return (base * repeats) + f"Task {marker}: reply with a 3-sentence status note."

def stream_one(args, marker, results, idx, barrier):
    prompt = build_prompt(args.prompt_tokens, marker) if not args.no_prefix \
        else f"Count from 1 to 5. Task {marker}."
    payload = json.dumps({
        "model": args.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": args.max_tokens,
        "temperature": 0.0,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        f"{args.base}/v1/chat/completions", data=payload,
        headers={"Content-Type": "application/json"})
    t_start = time.perf_counter()
    t_first = None
    n_tokens = 0
    try:
        barrier.wait()  # all threads fire together -> honest concurrency
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                chunk = line[6:]
                if chunk == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta.get("content") or delta.get("reasoning_content"):
                    if t_first is None:
                        t_first = time.perf_counter()
                    n_tokens += 1
        t_end = time.perf_counter()
        results[idx] = {
            "ok": True, "ttft_s": (t_first - t_start) if t_first else None,
            "tokens": n_tokens, "elapsed_s": t_end - t_start,
            "decode_tok_s": (n_tokens / (t_end - t_first)) if t_first and t_end > t_first else 0.0,
        }
    except Exception as e:  # noqa: BLE001 — record and continue
        results[idx] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                        "tokens": n_tokens, "elapsed_s": time.perf_counter() - t_start}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--model", default="enigma/default")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--requests", type=int, default=4)
    ap.add_argument("--prompt-tokens", type=int, default=4000,
                    help="approx shared-prefix size per request")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--no-prefix", action="store_true",
                    help="short prompts (pure decode, cold cache)")
    args = ap.parse_args()

    results = [None] * args.requests
    barrier = threading.Barrier(args.concurrency)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(stream_one, args, i, results, i, barrier)
                for i in range(args.requests)]
        for f in futs:
            f.result()
    wall = time.perf_counter() - t0

    print(f"{'req':>3} {'ok':>3} {'ttft_s':>8} {'tokens':>7} {'elapsed_s':>10} {'decode_t/s':>10}")
    oks, total_tok, ttfts, decode_rates = 0, 0, [], []
    for i, r in enumerate(results):
        if r and r.get("ok"):
            oks += 1
            total_tok += r["tokens"]
            if r["ttft_s"] is not None:
                ttfts.append(r["ttft_s"])
            if r["decode_tok_s"]:
                decode_rates.append(r["decode_tok_s"])
            print(f"{i:>3} {'Y':>3} {r['ttft_s'] or -1:>8.2f} {r['tokens']:>7} "
                  f"{r['elapsed_s']:>10.1f} {r['decode_tok_s']:>10.1f}")
        else:
            print(f"{i:>3} {'N':>3} {'-':>8} {r['tokens'] if r else 0:>7} "
                  f"{r['elapsed_s'] if r else 0:>10.1f} {'-':>10} {r.get('error') if r else '?'}")
    agg = total_tok / wall if wall else 0
    avg_ttft = sum(ttfts) / len(ttfts) if ttfts else -1
    avg_decode = sum(decode_rates) / len(decode_rates) if decode_rates else -1
    print(f"SUMMARY ok={oks}/{args.requests} concurrency={args.concurrency} "
          f"wall_s={wall:.1f} agg_tok_s={agg:.1f} avg_decode_tok_s={avg_decode:.1f} "
          f"avg_ttft_s={avg_ttft:.2f} total_tokens={total_tok}")
    sys.exit(0 if oks == args.requests else 1)

if __name__ == "__main__":
    main()
