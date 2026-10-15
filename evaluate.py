"""Tu cham diem: so sanh submission voi file dap an theo Exact Match.

    python evaluate.py --pred submission.csv --gold ../sample_submission.csv [--queries ../queries.json]
"""
import argparse
import csv
import json
import sys
from pathlib import Path


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r["id"]: r["answer"] for r in csv.DictReader(f)}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--queries", default=None)
    a = ap.parse_args()
    pred, gold = read(a.pred), read(a.gold)
    qs = {}
    if a.queries:
        qs = {q["id"]: q["query"] for q in json.loads(Path(a.queries).read_text(encoding="utf-8"))}
    ok = 0
    for i, g in gold.items():
        p = pred.get(i, "")
        if p == g:
            ok += 1
        else:
            print(f"[WRONG] {i}: pred={p!r} gold={g!r}" + (f"\n        Q: {qs[i]}" if i in qs else ""))
    print(f"\nExact match: {ok}/{len(gold)} = {ok / max(1, len(gold)):.1%}")


if __name__ == "__main__":
    main()
