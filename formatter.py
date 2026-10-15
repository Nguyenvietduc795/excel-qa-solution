"""Buoc 4 - Dinh dang dap an cuoi cung theo chi dan "Answer with ..." trong cau hoi (khong dung LLM).

Ho tro: Yes/No, True/False, so nguyen, N chu so thap phan (lam tron half-up), dau %, dau phay hang nghin,
dinh dang ngay, nhieu gia tri (", " hoac "; "). Ngoai ra kiem tra kieu/so luong ket qua de yeu cau LLM sua.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import ROUND_HALF_UP, Decimal

from table_parser import clean_text

_WORD_NUM = {"zero": 0, "no": 0, "one": 1, "a single": 1, "single": 1, "two": 2, "three": 3, "four": 4,
             "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM_WORD = r"(\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
_PLACES = {"tenth": 1, "hundredth": 2, "thousandth": 3}
_DATE_FORMATS = [  # (mau trong cau hoi, strftime)
    (r"yyyy-mm-dd", "%Y-%m-%d"), (r"yyyy/mm/dd", "%Y/%m/%d"), (r"mm/dd/yyyy", "%m/%d/%Y"),
    (r"dd/mm/yyyy", "%d/%m/%Y"), (r"dd-mm-yyyy", "%d-%m-%Y"), (r"mm-dd-yyyy", "%m-%d-%Y"),
    (r"dd\.mm\.yyyy", "%d.%m.%Y"), (r"yyyy-mm\b", "%Y-%m"), (r"month d+, yyyy", "%B %-d, %Y"),
    (r"d+ month yyyy", "%-d %B %Y"),
]


def _instruction(query: str) -> str:
    """Phan chi dan dinh dang (tu chu 'Answer'/'Provide'/'Return'... cuoi cung) - de dem so gia tri."""
    q = query.lower()
    m = list(re.finditer(r"\b(answer|provide|return|respond|give|list|report)\b", q))
    return q[m[-1].start():] if m else ""


def parse_spec(query: str) -> dict:
    q = query.lower()
    spec = {"yesno": False, "truefalse": False, "decimals": None, "integer": False,
            "percent_sign": False, "thousands": False, "date_fmt": None, "sep": ", ", "count": None}
    if re.search(r"\byes\b['\"]?\s*(or|/)\s*['\"]?\bno\b", q):
        spec["yesno"] = True
    elif re.search(r"\btrue\b['\"]?\s*(or|/)\s*['\"]?\bfalse\b", q):
        spec["truefalse"] = True
    m = re.search(_NUM_WORD + r"\s+decimal", q) or re.search(r"\b(\d+)\s*(d\.p\.|dp)\b", q)
    m2 = re.search(r"nearest (tenth|hundredth|thousandth)", q)
    if m:
        g = m.group(1)
        spec["decimals"] = int(g) if g.isdigit() else _WORD_NUM[g]
    elif m2:
        spec["decimals"] = _PLACES[m2.group(1)]
    elif re.search(r"\b(an |the |only the |two |three )?integers?\b|whole numbers?|no decimal|without decimal"
                   r"|nearest (unit|one)\b", q):
        spec["integer"] = True
    if spec["decimals"] == 0:
        spec["decimals"], spec["integer"] = None, True
    pct = r"(%|percent(age)? (sign|symbol))"
    if re.search(pct, q) and not re.search(r"(without|no|omit|exclude|excluding|not include|don't include)"
                                           r"[^.;]{0,30}" + pct, q):
        if re.search(r"\b(with|include|including|followed by|add|append|as)\b[^.;]{0,30}" + pct, q) \
                or re.search(r"e\.g\.?,?\s*['\"`]?-?[\d.]+%", q):
            spec["percent_sign"] = True
    if re.search(r"thousands?[- ]separators?|commas? (as|for) (the )?thousands?", q) \
            and not re.search(r"(without|no|omit|exclude)[^.;]{0,25}(thousands?[- ]separators?|commas)", q):
        spec["thousands"] = True
    for pat, fmt in _DATE_FORMATS:
        if re.search(pat, q):
            spec["date_fmt"] = fmt
            break
    instr = _instruction(query)
    if re.search(r"separated by (a )?semicolons?|semicolon[- ]separated", q):
        spec["sep"] = "; "
    elif re.search(r"comma(s)? (and )?(with )?no spaces?|comma without (a )?spaces?", q):
        spec["sep"] = ","
    m = re.search(r"\b" + _NUM_WORD + r"\s+((?!decimal)\w+\s+){0,3}?(integers|numbers|values|names|categories|"
                  r"items|words|labels|answers|percentages|rates|dates|years|amounts|counts|industries|sectors|"
                  r"occupations|groups|countries|regions|products|people|persons|states|cities|months)\b", instr)
    if m and m.group(1) not in ("zero", "no", "one"):
        spec["count"] = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUM[m.group(1)]
    tpl = re.search(r"[`'\"]([^`'\"]{1,60},[^`'\"]{1,60})[`'\"]", instr)       # vd "Answer as `men, women`"
    if spec["count"] is None and tpl:
        spec["count"] = tpl.group(1).count(",") + 1
    return spec


def _round_half_up(x: float, d: int) -> str:
    q = Decimal(1).scaleb(-d)
    # lam tron truoc o 9 chu so de khu sai so dau phay dong (3.1449999999999996 -> 3.145)
    return str(Decimal(repr(round(float(x), 9))).quantize(q, rounding=ROUND_HALF_UP))


def _group_thousands(s: str) -> str:
    neg = s.startswith("-")
    s = s.lstrip("-")
    ip, _, fp = s.partition(".")
    ip = f"{int(ip):,}"
    return ("-" if neg else "") + ip + ("." + fp if fp else "")


def _fmt_number(x, spec) -> str:
    if spec["decimals"] is not None:
        s = _round_half_up(x, spec["decimals"])
    elif spec["integer"]:
        s = _round_half_up(x, 0)
    elif float(x).is_integer():
        s = str(int(x))
    else:
        s = _round_half_up(x, 6).rstrip("0").rstrip(".")
    if re.fullmatch(r"-0(\.0*)?", s):
        s = s[1:]
    if spec["thousands"]:
        s = _group_thousands(s)
    return s + ("%" if spec["percent_sign"] else "")


def _to_number(s: str):
    t = s.strip().replace(",", "").rstrip("%").lstrip("$")
    try:
        return float(t)
    except ValueError:
        return None


def _fmt_date(v, spec) -> str:
    fmt = spec["date_fmt"] or "%Y-%m-%d"
    if "%-d" in fmt:                      # ngay khong co so 0 dau (khong dung %-d vi Windows khong ho tro)
        fmt = fmt.replace("%-d", str(v.day))
    return v.strftime(fmt)


def _fmt_one(v, spec) -> str:
    if isinstance(v, bool):
        if spec["truefalse"]:
            return "True" if v else "False"
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        if (spec["yesno"] or spec["truefalse"]) and v in (0, 1):
            return _fmt_one(bool(v), spec)
        return _fmt_number(v, spec)
    if isinstance(v, (dt.datetime, dt.date)):
        return _fmt_date(v, spec)
    s = str(v).strip()
    if spec["yesno"] or spec["truefalse"]:
        low = s.lower().strip(".! ")
        if low in ("yes", "true", "y", "có", "co", "đúng"):
            return _fmt_one(True, spec)
        if low in ("no", "false", "n", "không", "khong", "sai"):
            return _fmt_one(False, spec)
    num = _to_number(s)
    if num is not None and (spec["decimals"] is not None or spec["integer"] or spec["thousands"]
                            or spec["percent_sign"]):
        return _fmt_number(num, spec)
    return clean_text(s)


def _plain(result):
    if hasattr(result, "isoformat") and not isinstance(result, (dt.datetime, dt.date)):
        result = result.to_pydatetime()   # pandas Timestamp
    return result


def _words(s: str) -> list:
    return re.findall(r"[a-z0-9]+", s.lower())


def _question_labels(query: str) -> list:
    """Cac cum ten ngan liet ke trong cau hoi, vd 'Agriculture, Manufacturing, and Construction'."""
    parts = re.split(r"[,;:.?!()`'\"]|\band\b|\bor\b", query)
    return [p.strip() for p in parts if p.strip() and 1 <= len(_words(p)) <= 5]


def match_question_label(v, query: str):
    """Ten lay tu bang dai hon ten cau hoi da liet ke ('Agriculture and related' vs 'Agriculture')
    -> dung dung chu trong cau hoi. Chi doi khi chu cua ten trong cau hoi nam tron trong dap an."""
    if not isinstance(v, str) or not _words(v):
        return v
    if v.strip().lower() in query.lower():
        return v
    vw = _words(v)
    best = None
    for lab in _question_labels(query):
        lw = _words(lab)
        if lw and len(lw) < len(vw) <= len(lw) + 4 and vw[:len(lw)] == lw:
            if best is None or len(lw) > len(_words(best)):
                best = lab
    return best or v


def format_answer(result, query: str) -> str:
    spec = parse_spec(query)
    values = result if isinstance(result, list) else [result]
    values = [match_question_label(v, query) for v in values]
    return spec["sep"].join(_fmt_one(_plain(v), spec) for v in values)


def result_hint(query: str) -> str:
    """Mo ta kieu `result` mong doi (gui kem cau hoi cho LLM)."""
    spec = parse_spec(query)
    if spec["yesno"] or spec["truefalse"]:
        kind = "a bool (True/False)"
    elif spec["decimals"] is not None or spec["integer"]:
        kind = "a number (unrounded)"
    elif spec["date_fmt"]:
        kind = "a date (pd.Timestamp)"
    else:
        kind = None
    if spec["count"] and spec["count"] > 1:
        each = f", each {kind}" if kind else ""
        return f"a list of {spec['count']} values in the requested order{each}"
    return kind or ""


def type_problem(result, query: str) -> str | None:
    """Kiem tra ket qua co dung kieu/so luong ma cau hoi yeu cau; tra ve mo ta loi (de LLM sua) hoac None."""
    spec = parse_spec(query)
    values = result if isinstance(result, list) else [result]
    if spec["count"] and spec["count"] > 1 and len(values) != spec["count"]:
        return f"the question asks for {spec['count']} values but `result` has {len(values)}"
    for v in values:
        v = _plain(v)
        if spec["yesno"] or spec["truefalse"]:
            if isinstance(v, bool) or (isinstance(v, (int, float)) and v in (0, 1)):
                continue
            if isinstance(v, str) and v.strip().lower().strip(".! ") in ("yes", "no", "true", "false"):
                continue
            return f"the question asks for Yes/No, so `result` must be a bool, not {v!r}"
        if spec["decimals"] is not None or spec["integer"]:
            if isinstance(v, bool) or not (isinstance(v, (int, float)) or
                                           (isinstance(v, str) and _to_number(v) is not None)):
                return f"the question asks for a number, but `result` contains {v!r}"
    return None
