"""Excel QA - diem vao chuong trinh.

    python main.py --xlsx_dir <thu_muc_xlsx> --queries <queries.json> --output submission.csv

Luong xu ly moi cau hoi:
    [1] table_parser: chuan hoa file Excel  ->  [2] llm: LLM viet code pandas
    ->  [3] executor: chay code (loi / sai kieu thi gui phan hoi cho LLM sua)  ->  [4] formatter: dinh dang dap an
    Mac dinh 1 loi giai (temperature 0); that bai thi thu loi giai thu 2. --votes 3: bo phieu nhu ban cu.
    Tat ca that bai -> hoi thang dap an.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from executor import extract_code, run_code
from formatter import format_answer, result_hint, type_problem
from llm import LLMClient, LLMError, build_direct_messages, build_messages, load_dotenv
from table_parser import parse_workbook, render_tables

HERE = Path(__file__).resolve().parent


def find_file(xlsx_dir: Path, name: str) -> Path | None:
    p = xlsx_dir / name
    if p.is_file():
        return p
    hits = sorted(xlsx_dir.rglob(name))
    if hits:
        return hits[0]
    low = name.lower()
    for q in xlsx_dir.rglob("*"):
        if q.is_file() and q.name.lower() == low:
            return q
    return None


def solve_one(q, tables, client: LLMClient, args) -> dict:
    t0 = time.time()
    rec = {"id": q["id"], "file_name": q["file_name"], "query": q["query"], "answer": "",
           "attempts": [], "tokens": 0, "cost": 0.0}
    if not tables or client is None:
        rec["error"] = "no LLM client (missing API key)" if client is None else "file not found or no table parsed"
        rec["seconds"] = round(time.time() - t0, 2)
        return rec
    tables_text = render_tables(tables)
    lock = threading.Lock()

    def ask(messages, temperature):
        text, usage = client.chat(messages, temperature=temperature)
        with lock:
            rec["tokens"] += usage["total_tokens"]
            rec["cost"] += usage["cost"]
        return text

    def over_budget():
        return time.time() - t0 > args.question_timeout or rec["cost"] > args.max_cost

    def one_chain(chain, temperature):
        """1 chuoi: LLM viet code -> chay -> (loi / sai kieu) -> gui phan hoi de sua, toi da max_retries lan."""
        messages = build_messages(tables_text, q["query"], result_hint(q["query"]))
        fallback = None
        for attempt in range(args.max_retries + 1):
            if over_budget():
                break
            try:
                code = extract_code(ask(messages, temperature))
            except LLMError as e:
                with lock:
                    rec["attempts"].append({"chain": chain, "error": str(e)[:300]})
                break
            step = {"chain": chain, "code": code}
            with lock:
                rec["attempts"].append(step)
            try:
                res = run_code(code, tables, timeout=args.exec_timeout)
                step["result"] = repr(res)[:300]
                ans = format_answer(res, q["query"])
                problem = type_problem(res, q["query"])
                if problem is None:
                    return ans
                step["warning"] = problem
                fallback = fallback or ans
                feedback = (f"Your code ran and set result = {repr(res)[:300]}, but {problem}.\n"
                            "Re-check the question and the table, then reply with ONE ```python code block "
                            "that sets `result`.")
            except Exception as e:  # noqa: BLE001 - ExecError hoac loi dinh dang
                step["error"] = str(e)[:500]
                feedback = (f"Running your code failed: {e}\n"
                            "Fix it and reply with ONE ```python code block that sets `result`.")
            messages = messages + [{"role": "assistant", "content": f"```python\n{code}\n```"},
                                   {"role": "user", "content": feedback}]
        return fallback

    votes = max(1, args.votes)
    temps = [0.0] + [0.6] * max(1, votes - 1)
    answers = []
    if votes == 1:
        # mac dinh: chi 1 chuoi temperature 0 (chuoi 0.6 hay bo qua luat trong prompt va thang phieu sai).
        # Chuoi 0 that bai han -> thu 1 chuoi temperature 0.6 truoc khi hoi thang dap an.
        for k in range(2):
            if k and (answers or over_budget()):
                break
            a = one_chain(k, temps[k])
            if a is not None:
                answers.append(a)
    else:
        # 2 chuoi dau chay song song; neu trung nhau thi dung, neu khac thi chay them de lay da so
        first = min(2, votes)
        with cf.ThreadPoolExecutor(max_workers=first) as pool:
            answers = [a for a in pool.map(one_chain, range(first), temps[:first]) if a is not None]
        for k in range(first, votes):
            if answers and Counter(answers).most_common(1)[0][1] > votes // 2:
                break
            if over_budget():
                break
            a = one_chain(k, temps[k])
            if a is not None:
                answers.append(a)
    if answers:
        rec["answer"] = Counter(answers).most_common(1)[0][0]   # hoa phieu -> uu tien chuoi temperature 0
        rec["votes"] = answers
    elif args.direct_fallback and not over_budget():
        # code that bai het -> hoi thang dap an, van dinh dang bang formatter
        try:
            text = ask(build_direct_messages(tables_text, q["query"]), 0.0).strip()
            line = text.strip("`").strip().splitlines()[-1].strip() if text.strip("`").strip() else ""
            if line:
                rec["answer"] = format_answer(line, q["query"])
                rec["direct_answer"] = line
        except LLMError as e:
            rec["error"] = str(e)
    if not rec["answer"] and "error" not in rec:
        errs = [a.get("error") for a in rec["attempts"] if a.get("error")]
        if errs:
            rec["error"] = errs[-1]
    rec["seconds"] = round(time.time() - t0, 2)
    rec["cost"] = round(rec["cost"], 6)
    return rec


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Answer natural-language questions over Excel files")
    ap.add_argument("--xlsx_dir", required=True, help="thu muc chua cac file xlsx (tim de quy)")
    ap.add_argument("--queries", required=True, help="duong dan queries.json")
    ap.add_argument("--output", default="submission.csv")
    ap.add_argument("--model", default=None, help="mac dinh qwen/qwen3.7-flash (hoac OPENROUTER_MODEL)")
    ap.add_argument("--reasoning", default="off", choices=["off", "low", "medium", "high", "default"])
    ap.add_argument("--workers", type=int, default=8, help="so cau hoi xu ly song song")
    ap.add_argument("--votes", type=int, default=1,
                    help="1 (mac dinh) = 1 loi giai temperature 0, chi thu loi giai thu 2 khi loi giai dau that bai;"
                         " >1 = bo phieu: 2 chuoi dau chay song song, chi chay them khi 2 dap an khac nhau")
    ap.add_argument("--no_direct_fallback", dest="direct_fallback", action="store_false",
                    help="tat buoc du phong hoi thang dap an khi moi lan chay code deu loi")
    ap.add_argument("--max_retries", type=int, default=2, help="so lan sua code khi chay loi")
    ap.add_argument("--exec_timeout", type=float, default=15.0)
    ap.add_argument("--question_timeout", type=float, default=100.0)
    ap.add_argument("--max_cost", type=float, default=0.08, help="USD toi da moi cau (gioi han BTC 0.10)")
    ap.add_argument("--log_dir", default=None, help="ghi log chi tiet tung cau (JSON)")
    args = ap.parse_args(argv)

    load_dotenv(Path.cwd() / ".env", HERE / ".env")
    t_start = time.time()
    xlsx_dir = Path(args.xlsx_dir)
    queries = json.loads(Path(args.queries).read_text(encoding="utf-8"))

    # [1] chuan hoa moi file 1 lan
    parsed = {}
    for name in dict.fromkeys(q["file_name"] for q in queries):
        path = find_file(xlsx_dir, name)
        try:
            parsed[name] = parse_workbook(path) if path else []
        except Exception as e:  # noqa: BLE001
            print(f"[warn] cannot parse {name}: {e}")
            parsed[name] = []
        if not path:
            print(f"[warn] file not found: {name}")

    try:
        client = LLMClient(model=args.model, reasoning=args.reasoning)
    except LLMError as e:          # van ghi submission.csv (rong) de bai nop hop le
        print(f"[error] {e}")
        client = None
    results = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(solve_one, q, parsed[q["file_name"]], client, args): q for q in queries}
        for fut in cf.as_completed(futs):
            q = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                rec = {"id": q["id"], "answer": "", "error": f"{type(e).__name__}: {e}",
                       "tokens": 0, "seconds": 0, "cost": 0}
            results[q["id"]] = rec
            print(f"{rec['id']}: {rec['answer']!r}  ({rec.get('seconds', 0)}s, {rec.get('tokens', 0)} tok)"
                  + (f"  ERROR {rec['error']}" if rec.get("error") else ""))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "answer"])
        for q in queries:
            ans = " ".join(str(results[q["id"]]["answer"] or "").split())  # dam bao 1 dong / id
            w.writerow([q["id"], ans])

    if args.log_dir:
        d = Path(args.log_dir)
        d.mkdir(parents=True, exist_ok=True)
        for rid, rec in results.items():
            (d / f"{rid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    n = max(1, len(queries))
    tok = sum(r.get("tokens", 0) for r in results.values())
    cost = sum(r.get("cost", 0) for r in results.values())
    print(f"\nWrote {out}  |  {len(queries)} questions  |  wall {time.time() - t_start:.1f}s  |  "
          f"avg {sum(r.get('seconds', 0) for r in results.values()) / n:.1f}s/question  |  "
          f"avg {tok / n:.0f} tokens/question  |  total cost ${cost:.4f}")


if __name__ == "__main__":
    main()
