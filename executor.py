"""Buoc 3 - Chay doan code pandas do LLM sinh ra trong moi truong han che."""
from __future__ import annotations

import builtins
import concurrent.futures as cf
import datetime
import math
import re
import statistics

import numpy as np
import pandas as pd

_ALLOWED_MODULES = {"pandas", "numpy", "math", "re", "datetime", "statistics", "collections",
                    "itertools", "functools", "operator", "decimal", "fractions", "dateutil"}
_BLOCKED = {"open", "exec", "eval", "compile", "input", "breakpoint", "exit", "quit",
            "globals", "locals", "vars", "memoryview", "help"}
_POOL = cf.ThreadPoolExecutor(max_workers=48, thread_name_prefix="exec")   # 20 cau x 2 loi giai song song


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in _ALLOWED_MODULES:
        raise ImportError(f"import of '{name}' is not allowed")
    return __import__(name, globals, locals, fromlist, level)


_SAFE_BUILTINS = {k: getattr(builtins, k) for k in dir(builtins) if k not in _BLOCKED}
_SAFE_BUILTINS["__import__"] = _safe_import
_SAFE_BUILTINS["print"] = lambda *a, **k: None      # code LLM hay print de go loi -> bo qua, khong lan vao output


class ExecError(Exception):
    pass


def extract_code(text: str) -> str:
    """Lay khoi ```python ...``` (hoac toan bo text neu khong co).
    Chiu duoc: khoi khong dong ``` (bi cat do het token), dong dau chi ghi 'python' (```\\npython\\n...)."""
    blocks = re.findall(r"```[ \t]*(?:python3?|py)?[ \t]*\n(.*?)(?:```|\Z)", text, re.S)
    code = max(blocks, key=len) if blocks else text.strip().strip("`")
    code = re.sub(r"^\s*(?:python3?|py)\s*\n", "", code)
    return code.strip()


def normalize_result(v):
    """Chuyen Series/ndarray/numpy scalar... ve kieu Python co ban."""
    if isinstance(v, pd.DataFrame):
        if v.size == 1:
            return normalize_result(v.iat[0, 0])
        raise ExecError(f"result is a DataFrame with shape {v.shape}; return a scalar or a list")
    if isinstance(v, dict):
        v = list(v.values())
    if isinstance(v, (pd.Series, pd.Index, np.ndarray)):
        v = list(v)
    if isinstance(v, (list, tuple, set)):
        v = [normalize_result(x) for x in v]
        return v[0] if len(v) == 1 else v
    if isinstance(v, np.generic):
        return v.item()
    return v


def _is_missing(v) -> bool:
    if v is None or v is pd.NaT:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, list):
        return len(v) == 0 or any(_is_missing(x) for x in v)
    return False


# ----------------------------------------------------------------------------
# Tu sua loi KeyError pho bien nhat: LLM goi cot bang ten tieu de ('Region', 'Date of | Admission') thay vi
# chu cai cot, hoac goi dong bang nhan ('Construction') thay vi so dong Excel. Sua ngay trong may (khong goi lai
# LLM) neu xac dinh duoc duy nhat; neu khong thi tra ve goi y cu the de LLM sua.
def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _column_matches(key, tables):
    k = _norm(key)
    exact, loose = [], []
    for ti, t in enumerate(tables):
        for letter, name in t.columns.items():
            parts = [_norm(p) for p in name.split(" | ")]
            if k in (_norm(name), parts[-1]) or k in parts:
                exact.append((ti, letter, name))
            elif len(k) > 3 and k in _norm(name):
                loose.append((ti, letter, name))
    return exact or loose


def _row_matches(key, tables):
    k = _norm(key)
    out = []
    for ti, t in enumerate(tables):
        for c in t.columns:
            rows = [i for i, v in t.df[c].items() if isinstance(v, str) and _norm(v) == k]
            if rows:
                out.append((ti, c, rows))
    return out


def _missing_keys(e: Exception) -> list:
    if not isinstance(e, KeyError) or not e.args:
        return []
    k = e.args[0]
    if isinstance(k, str):
        m = re.match(r"Column not found: (.+)$", k)           # loi cua groupby(...)['X']
        if m:
            return [m.group(1).strip()]
        m = re.match(r"None of \[(.*)\] are in the", k)
        if m:                                 # "None of [Index(['Teacher'], dtype='object')] are in the [columns]"
            inner = re.sub(r",\s*dtype='[^']*'", "", m.group(1))
            return re.findall(r"'([^']*)'", inner) or [inner]
    return [k]


def _quoted(key: str) -> str:
    return r"(?P<q>['\"])" + re.escape(key) + r"(?P=q)"


# chuoi dung lam GIA TRI de so sanh/tim kiem (khong phai ten cot) -> khong thay
_VALUE_CTX = re.compile(r"(==|!=|\bin|contains\(|startswith\(|endswith\(|isin\(\s*\[?|replace\(|match\(|search\()"
                        r"\s*$")


def _replace_key(code: str, key: str, letter: str) -> str:
    """Thay moi chuoi 'key' trong code bang 'letter' (df['Region'], groupby('Region'), col = 'Region'...),
    tru cho chuoi dang duoc so sanh nhu mot gia tri (df['B'] == 'Region')."""
    out, last = [], 0
    for m in re.finditer(_quoted(key), code):
        if _VALUE_CTX.search(code[max(0, m.start() - 25):m.start()]):
            continue
        out += [code[last:m.start()], f"'{letter}'"]
        last = m.end()
    return "".join(out) + code[last:]


def _autofix(code: str, keys: list, tables) -> tuple[str, list]:
    """Doi ten cot -> chu cai cot, nhan dong (duy nhat) -> so dong trong code. Tra ve (code moi, mo ta)."""
    fixes = []
    for key in keys:
        if not isinstance(key, str):
            continue
        cols = _column_matches(key, tables)
        letters = {letter for _, letter, _ in cols}
        if len(letters) == 1:
            letter = letters.pop()
            new = _replace_key(code, key, letter)
            if new != code:
                code = new
                fixes.append(f"column '{key}' -> '{letter}'")
                continue
        rows = _row_matches(key, tables)
        if len(rows) == 1 and len(rows[0][2]) == 1:
            r = rows[0][2][0]
            new = re.sub(r"(?P<pre>\.loc\[\s*)" + _quoted(key), lambda m: f"{m.group('pre')}{r}", code)
            if new != code:
                code = new
                fixes.append(f"row '{key}' -> {r}")
    if any(f.startswith("column") for f in fixes):
        # LLM da dung ten cot o mot cho -> thuong dung ca danh sach ten (vd 12 nganh): doi het trong 1 luot
        for lit in sorted(set(re.findall(r"['\"]([^'\"\n]{3,90})['\"]", code)), key=len, reverse=True):
            if re.fullmatch(r"[A-Z]{1,3}", lit):
                continue
            letters = {letter for _, letter, name in _column_matches(lit, tables)
                       if _norm(lit) in (_norm(name), _norm(name.split(" | ")[-1]))}
            if len(letters) == 1:
                new = _replace_key(code, lit, next(iter(letters)))
                if new != code:
                    code = new
                    fixes.append(f"column '{lit}' -> '{next(iter(letters))}'")
    return code, fixes


def _key_hint(keys: list, tables) -> str:
    hints = []
    for key in keys:
        cols = _column_matches(key, tables) if isinstance(key, str) else []
        rows = _row_matches(key, tables) if isinstance(key, str) else []
        if cols:
            where = "; ".join(f"column {letter} of tables[{ti}] ('{name}')" for ti, letter, name in cols[:4])
            hints.append(f"'{key}' is a column header, not a key: access columns by Excel letter only -> {where}.")
        if rows:
            where = "; ".join(f"tables[{ti}] column {c}, Excel row(s) {', '.join(map(str, rs[:6]))}"
                              for ti, c, rs in rows[:3])
            hints.append(f"'{key}' is a row label, not an index: the index is the Excel row number -> {where} "
                         f"(use df.loc[row, letter] or df[df['{rows[0][1]}'] == '{key}']).")
        if isinstance(key, pd.Index):
            hints.append("sort_values(by=...) / df[...] take column letters only (not df.index); "
                         "to keep table order for ties use sort_values(..., kind='stable').")
        if isinstance(key, (int, np.integer)):
            spans = "; ".join(f"tables[{ti}] has Excel rows {t.df.index.min()}..{t.df.index.max()}"
                              for ti, t in enumerate(tables) if len(t.df))
            hints.append(f"Row {key} does not exist in the DataFrame you indexed ({spans}); re-check the table.")
    if not hints and tables:
        hints.append("Columns are Excel letters (" + ", ".join(tables[0].columns) + ") plus 'section'; "
                     "the index is the Excel row number.")
    return " ".join(hints)


def run_code(code: str, tables: list, timeout: float = 15.0, info: dict | None = None):
    """Chay code LLM. info (neu truyen) nhan 'auto_fix' (danh sach sua tu dong) va 'code' (code cuoi cung)."""
    def col_name(letter, table=0):
        name = tables[table].columns[letter]
        return name.split(" | ")[-1]

    # df[col_name('F')] -> df['F']: col_name chi dung de TRA VE ten cot, khong dung de truy cap cot
    code = re.sub(r"(?P<pre>\[\s*|,\s*)col_name\(\s*(?P<q>['\"])(?P<l>[A-Z]{1,3})(?P=q)[^()\]]*\)(?=\s*[\],])",
                  lambda m: f"{m.group('pre')}'{m.group('l')}'", code)
    fixes = []
    for _ in range(6):                       # vai vong tu sua KeyError
        frames = [t.df.copy() for t in tables]
        env = {
            "col_name": col_name,
            "__builtins__": _SAFE_BUILTINS,
            "pd": pd, "np": np, "math": math, "re": re, "datetime": datetime,
            "statistics": statistics,
            "tables": frames, "df": frames[0] if frames else None,
            "sheets": [t.sheet for t in tables],          # sheets[i] = ten sheet cua tables[i]
            "result": None,
        }

        def _run(env=env, code=code):
            exec(compile(code, "<llm_code>", "exec"), env)
            return env.get("result")

        fut = _POOL.submit(_run)
        try:
            raw = fut.result(timeout=timeout)
            break
        except cf.TimeoutError:
            raise ExecError(f"code did not finish within {timeout}s")
        except Exception as e:  # noqa: BLE001
            keys = _missing_keys(e)
            new_code, done = _autofix(code, keys, tables) if keys else (code, [])
            if done:
                code, fixes = new_code, fixes + done
                continue
            msg = f"{type(e).__name__}: {str(e)[:300]}"
            if keys:
                msg += "\nHint: " + _key_hint(keys, tables)
                if "col_name(" in code:
                    msg += (" Do not use col_name() to read data: it returns display text only; index with the "
                            "letter itself (e.g. row['K'] or df.loc[9, 'B':'M']).")
            elif "truth value of a Series is ambiguous" in str(e):
                msg += "\nHint: a filter returned several rows; select one value with .iloc[0] or use .any()/.all()."
            raise ExecError(msg) from e
    else:
        raise ExecError("could not repair column/row keys automatically; " + _key_hint([], tables))
    if info is not None:
        info["auto_fix"], info["code"] = fixes, code
    res = normalize_result(raw)
    if _is_missing(res):
        raise ExecError(f"`result` is empty or missing (got {raw!r})")
    # LLM tra ve chu cai cot thay vi ten cot -> doi sang ten (tru khi chu do la gia tri that trong bang, vd hang "A")
    def _fix(v):
        if not isinstance(v, str) or not re.fullmatch(r"[A-Z]{1,2}", v):
            return v
        if any(t.df.isin([v]).any().any() for t in tables):
            return v
        for k, t in enumerate(tables):
            if v in t.columns and len(t.columns) > 1 and v != t.columns[v]:
                return col_name(v, k)
        return v
    res = [_fix(x) for x in res] if isinstance(res, list) else _fix(res)
    return res
