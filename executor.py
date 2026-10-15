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
_POOL = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="exec")


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split(".")[0] not in _ALLOWED_MODULES:
        raise ImportError(f"import of '{name}' is not allowed")
    return __import__(name, globals, locals, fromlist, level)


_SAFE_BUILTINS = {k: getattr(builtins, k) for k in dir(builtins) if k not in _BLOCKED}
_SAFE_BUILTINS["__import__"] = _safe_import


class ExecError(Exception):
    pass


def extract_code(text: str) -> str:
    """Lay khoi ```python ...``` (hoac toan bo text neu khong co)."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    if blocks:
        return max(blocks, key=len).strip()
    return text.strip().strip("`")


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


def _column_hint(err: KeyError, tables: list) -> str:
    """LLM goi cot bang ten ('Date of | Birth', 'Score') -> chi ra chu cai cot dung."""
    key = err.args[0] if err.args else None
    if not isinstance(key, str):
        return ""
    low = key.strip().lower()
    for k, t in enumerate(tables):
        for letter, name in t.columns.items():
            if low in (name.lower(), name.split(" | ")[-1].strip().lower()):
                where = f"tables[{k}]['{letter}']" if k else f"df['{letter}']"
                return f". Columns are Excel letters: '{key}' is column '{letter}', use {where}."
    return ""


def run_code(code: str, tables: list, timeout: float = 15.0):
    frames = [t.df.copy() for t in tables]

    def col_name(letter, table=0):
        name = tables[table].columns[letter]
        return name.split(" | ")[-1]

    env = {
        "col_name": col_name,
        "__builtins__": _SAFE_BUILTINS,
        "pd": pd, "np": np, "math": math, "re": re, "datetime": datetime,
        "statistics": statistics,
        "print": lambda *a, **k: None,   # code LLM hay print de debug -> khong in ra man hinh
        "tables": frames, "df": frames[0] if frames else None,
        "result": None,
    }

    def _run():
        exec(compile(code, "<llm_code>", "exec"), env)
        return env.get("result")

    fut = _POOL.submit(_run)
    try:
        raw = fut.result(timeout=timeout)
    except cf.TimeoutError:
        raise ExecError(f"code did not finish within {timeout}s")
    except KeyError as e:
        raise ExecError(f"KeyError: {e}{_column_hint(e, tables)}") from e
    except Exception as e:  # noqa: BLE001
        raise ExecError(f"{type(e).__name__}: {e}") from e
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
