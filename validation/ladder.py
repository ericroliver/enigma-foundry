#!/usr/bin/env python3
"""Long-context retrieval ladder — recipe gate #5 (Qwen3.8-Flash-Next Forge recipe).

Read-only against the server. Probes rungs (default 8K..250K) with a natural-
language corpus (Project Gutenberg, downloaded to corpus/ on first use) and a
needle sentence planted at several depths. Per rung also runs a no-needle
control to distinguish "missed the needle" from "confabulates under pressure".

Per probe records: exact prompt tokens (via /tokenize), elapsed, prefill tps,
finish reason, hit/miss, raw answer. Per rung: host MemAvailable/swap before
and after (via ssh, parsed locally). Prompts are non-thinking so failures
cannot hide behind reasoning.

Every probe uses a distinct corpus offset (seeded) so prefix caching cannot
make later prefills artificially warm. Corpus books are concatenated, which
mirrors real agent contexts (heterogeneous concatenated text).

Usage:
  python3 ladder.py                                  # full ladder vs localhost
  python3 ladder.py --base http://spark:8000 --rungs 64000 --positions 50,90
"""
import argparse, json, os, random, re, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS_DIR = os.path.join(HERE, "corpus")
BOOKS = [
    ("moby-dick", "https://www.gutenberg.org/cache/epub/2701/pg2701.txt"),
    ("pride-and-prejudice", "https://www.gutenberg.org/cache/epub/1342/pg1342.txt"),
    ("sherlock-holmes", "https://www.gutenberg.org/cache/epub/1661/pg1661.txt"),
    ("tale-of-two-cities", "https://www.gutenberg.org/cache/epub/98/pg98.txt"),
]
CODEWORDS = ["EMBER", "HARVEST", "LANTERN", "QUARRY", "TIMBER", "VESSEL",
             "COPPER", "MEADOW", "ANVIL", "BEACON", "CINDER", "DERRICK"]
SYSTEM = "You are a precise document-retrieval assistant."
NEEDLE_TMPL = ('In the margin of the page, a clerk had pencilled the codeword '
               '"{codeword}" beside the date.')
ASK_NEEDLE = ('A codeword in the form WORD-NNN was pencilled in the margin of '
              'one page of this text. What is the codeword? '
              'Answer with the codeword alone, no explanation.')
ASK_CONTROL = ('Was any codeword in the form WORD-NNN pencilled in the margin '
               'of this text? Answer "none" if there is no codeword, otherwise '
               'answer with the codeword alone.')
CODEWORD_RE = re.compile(r"[A-Z]{3,}-\d{3}")


def request(url, payload, timeout=1800):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def count_tokens(base, text):
    return request(base + "/tokenize",
                   {"model": "enigma/default", "prompt": text})["count"]


def load_corpus():
    os.makedirs(CORPUS_DIR, exist_ok=True)
    parts = []
    for name, url in BOOKS:
        path = os.path.join(CORPUS_DIR, name + ".txt")
        if not os.path.exists(path):
            print(f"downloading corpus: {name}", flush=True)
            urllib.request.urlretrieve(url, path)
        raw = open(path, encoding="utf-8", errors="replace").read()
        start = raw.find("*** START OF THE PROJECT GUTENBERG EBOOK")
        start = raw.find("\n", start) + 1 if start >= 0 else 0
        end = raw.find("*** END OF THE PROJECT GUTENBERG EBOOK")
        parts.append(raw[start:end if end > start else len(raw)])
    return "\n\n".join(parts)


def build(base, corpus, target, codeword, pos_frac, rng):
    """A target-token doc from corpus, needle at pos_frac (None = control)."""
    chars = int(target * 4.0)  # english prose ~4 chars/token; converge below
    start = rng.randrange(0, max(1, len(corpus) - chars - 1))
    needle = NEEDLE_TMPL.format(codeword=codeword) if codeword else ""
    ask = ASK_NEEDLE if codeword else ASK_CONTROL

    def assemble(body):
        if codeword:
            cut = int(len(body) * pos_frac)
            body = body[:cut] + " " + needle + " " + body[cut:]
        return body + "\n\n" + ask

    body = corpus[start:start + chars]
    text = assemble(body)
    n = count_tokens(base, text)
    for _ in range(30):
        if n == target:
            break
        if n > target:
            body = body[:int(len(body) * (target / n) * 0.995)]
        else:
            need = int((target - n) * 4.2)
            more = corpus[start + len(body):start + len(body) + need]
            if not more:  # corpus exhausted: wrap
                more = corpus[:need]
            body += more
        text = assemble(body)
        n = count_tokens(base, text)
    return text


def _to_gb(s):
    units = {"K": 1 / (1024 ** 2), "M": 1 / 1024, "G": 1.0, "T": 1024.0}
    s = s.strip()
    if not s:
        return None
    if s[-1] in units:
        return float(s[:-1]) * units[s[-1]]
    return float(s) / (1024 ** 3)  # bare number = bytes


def meminfo():
    """(MemAvailable GiB, swap used GiB) from the serving host, parsed locally."""
    try:
        mem = subprocess.run(["ssh", "spark", "cat /proc/meminfo"],
                             capture_output=True, text=True, timeout=20).stdout
        avail = float(re.search(r"MemAvailable:\s+(\d+)", mem).group(1)) / (1024 ** 2)
        sw = subprocess.run(["ssh", "spark", "swapon --show --noheadings"],
                            capture_output=True, text=True, timeout=20).stdout.split()
        # fields: NAME TYPE SIZE USED PRIO (units may be human or raw)
        swap = _to_gb(sw[3]) if len(sw) >= 4 else 0.0
        return round(avail, 1), round(swap, 2)
    except Exception:
        return None, None


def probe(base, doc, codeword):
    payload = {"model": "enigma/default", "max_tokens": 32, "temperature": 0,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": doc}],
               "chat_template_kwargs": {"enable_thinking": False}}
    t = time.time()
    r = request(base + "/v1/chat/completions", payload)
    dt = time.time() - t
    c = r["choices"][0]
    content = (c["message"].get("content") or "").strip()
    if codeword:
        hit = codeword in content
    else:  # control: pass = no fabricated codeword
        hit = not CODEWORD_RE.search(content)
    return hit, content, c.get("finish_reason"), r["usage"], dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rungs", default="8000,32000,64000,128000,200000,250000")
    ap.add_argument("--positions", default="10,25,50,75,90,97,none",
                    help="needle depth percent, or 'none' for no-needle control")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--seed", type=int, default=20260902)
    ap.add_argument("--outdir", default=os.path.expanduser("~/validation-results"))
    args = ap.parse_args()
    rungs = [int(r) for r in args.rungs.split(",")]
    positions = [None if p == "none" else int(p) / 100 for p in args.positions.split(",")]
    os.makedirs(args.outdir, exist_ok=True)
    corpus = load_corpus()
    print(f"corpus: {len(corpus) / 1e6:.1f} MB", flush=True)

    results, misses = [], []
    for rung in rungs:
        mem_a, swap_a = meminfo()
        t0 = time.time()
        for pos in positions:
            rng = random.Random(args.seed + rung * 100 + int((pos or 0) * 100))
            codeword = None if pos is None else (
                f"{rng.choice(CODEWORDS)}-{rng.randint(100, 999)}")
            doc = build(args.base, corpus, rung, codeword, pos, rng)
            hit, content, finish, usage, dt = probe(args.base, doc, codeword)
            label = "control" if pos is None else f"{int(pos * 100)}%"
            rec = {"rung": rung, "pos": label, "codeword": codeword, "hit": hit,
                   "finish": finish, "prompt_tok": usage["prompt_tokens"],
                   "completion_tok": usage["completion_tokens"],
                   "elapsed_s": round(dt, 2),
                   "prefill_tps": round(usage["prompt_tokens"] / dt, 1),
                   "content": content[:120]}
            results.append(rec)
            if not hit:
                misses.append(rec)
            print(f"[{rung:>6} {label:>7}] {'PASS' if hit else 'FAIL'} "
                  f"{usage['prompt_tokens']} tok in {dt:.1f}s "
                  f"({usage['prompt_tokens'] / dt:.0f} tok/s) finish={finish} "
                  f"-> {content[:70]!r}", flush=True)
        mem_b, swap_b = meminfo()
        results.append({"rung": rung, "rung_elapsed_s": round(time.time() - t0, 1),
                        "mem_avail_gb": [mem_a, mem_b],
                        "swap_used_gb": [swap_a, swap_b]})
        print(f"== rung {rung} done in {time.time() - t0:.0f}s | "
              f"mem {mem_a}->{mem_b} GiB | swap {swap_a}->{swap_b} GiB ==", flush=True)

    n_probes = len([r for r in results if "hit" in r])
    verdict = "PASS" if not misses else f"FAIL ({len(misses)} of {n_probes} probes)"
    print(f"\nVERDICT: {verdict}")
    path = os.path.join(args.outdir, "ladder-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w") as f:
        json.dump({"args": vars(args), "verdict": verdict, "misses": misses,
                   "results": results}, f, indent=1)
    print(f"saved: {path}")
    sys.exit(0 if not misses else 1)


if __name__ == "__main__":
    main()
