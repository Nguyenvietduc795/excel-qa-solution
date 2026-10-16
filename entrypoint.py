"""Diem vao cho Docker: tu tim du lieu dau vao roi goi main.py.

Cac cach goi (tat ca deu hop le):
    docker run ... <image> --xlsx_dir /data/xlsx_files --queries /data/queries.json --output /output/submission.csv
    docker run ... <image> /data [/output/submission.csv]        # thu muc chua queries.json + cac file xlsx
    docker run -v <du_lieu>:/data -v <ket_qua>:/output <image>    # khong tham so: tu tim

Bien moi truong (tuy chon): INPUT_DIR / DATA_DIR, XLSX_DIR, QUERIES, OUTPUT.
Khong tham so: tim queries.json trong INPUT_DIR, /data, /input, /app/data, /workspace, thu muc hien tai.
Ket qua: OUTPUT, hoac /output/submission.csv neu co thu muc /output, hoac submission.csv canh queries.json;
luon chep them 1 ban ra submission.csv o thu muc hien tai.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import main as solver

SEARCH_DIRS = ["/data", "/input", "/app/data", "/workspace"]


def _find_queries(d: Path) -> Path | None:
    if d.is_file():
        return d
    if not d.is_dir():
        return None
    if (d / "queries.json").is_file():
        return d / "queries.json"
    hits = sorted(d.rglob("queries.json"), key=lambda p: len(p.parts))
    return hits[0] if hits else None


def _writable_dir(d: Path) -> bool:
    return d.is_dir() and os.access(d, os.W_OK)


def resolve(argv: list[str]) -> list[str]:
    if any(a.startswith("--xlsx_dir") or a.startswith("--queries") for a in argv):
        return argv                                     # da du tham so -> giu nguyen
    n = 0                                               # tham so vi tri chi nam truoc cac co "--..."
    while n < len(argv) and n < 2 and not argv[n].startswith("-"):
        n += 1
    pos, rest = argv[:n], argv[n:]
    env_in = os.getenv("INPUT_DIR") or os.getenv("DATA_DIR")
    cands = ([pos[0]] if pos else []) + ([env_in] if env_in else []) + SEARCH_DIRS + [os.getcwd()]
    queries = Path(os.environ["QUERIES"]) if os.getenv("QUERIES") else None
    if queries is None:
        for c in cands:
            queries = _find_queries(Path(c))
            if queries:
                break
    if queries is None:
        sys.exit("Khong tim thay queries.json: truyen --xlsx_dir/--queries, hoac mount du lieu vao /data")
    xlsx_dir = Path(os.getenv("XLSX_DIR") or (pos[0] if pos and Path(pos[0]).is_dir() else queries.parent))
    if os.getenv("OUTPUT"):
        output = Path(os.environ["OUTPUT"])
    elif len(pos) > 1:
        output = Path(pos[1])
    elif _writable_dir(Path("/output")):
        output = Path("/output/submission.csv")
    elif _writable_dir(queries.parent):
        output = queries.parent / "submission.csv"
    else:
        output = Path.cwd() / "submission.csv"
    if output.is_dir():
        output = output / "submission.csv"
    print(f"[entrypoint] xlsx_dir={xlsx_dir} queries={queries} output={output}", flush=True)
    return ["--xlsx_dir", str(xlsx_dir), "--queries", str(queries), "--output", str(output)] + rest


def run(argv: list[str]) -> None:
    args = resolve(argv)
    solver.main(args)
    out = Path(args[args.index("--output") + 1]).resolve()
    local = (Path.cwd() / "submission.csv").resolve()
    if out != local and out.is_file():
        try:
            shutil.copyfile(out, local)
        except OSError:
            pass


if __name__ == "__main__":
    run(sys.argv[1:])
