"""Buoc 4 - Dinh dang dap an cuoi cung theo chi dan "Answer with ..." trong cau hoi (khong dung LLM).

Ho tro (cau hoi tieng Anh va tieng Viet): Yes/No, True/False, Có/Không, Đúng/Sai, so nguyen, N chu so thap phan
(lam tron half-up), dau %, dau phay hang nghin, dinh dang ngay, nhieu gia tri (", " hoac "; ").
Ngoai ra kiem tra kieu/so luong ket qua de yeu cau LLM sua.
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


_VI_NUM = {"một": 1, "hai": 2, "ba": 3, "bốn": 4}           # khong dung "năm" (vua la 5 vua la "nam")
_VI_PLACES = {"mười": 1, "trăm": 2, "nghìn": 3, "ngàn": 3}

# so chu so thap phan - ngon ngu khac (BTC: cau hoi "co the o cac ngon ngu khac nhau")
_ML_NUM = {"un": 1, "une": 1, "uno": 1, "una": 1, "um": 1, "uma": 1, "eine": 1, "einer": 1, "deux": 2, "dos": 2,
           "duas": 2, "dois": 2, "zwei": 2, "trois": 3, "tres": 3, "três": 3, "drei": 3, "quatre": 4, "cuatro": 4,
           "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "첫째": 1, "둘째": 2, "셋째": 3, "넷째": 4}
_ML_DECIMALS = [
    r"(\d+|une?|deux|trois|quatre)\s+(?:chiffres?\s+après\s+la\s+virgule|décimales?)",       # Phap
    r"(\d+|un[oa]?|dos|tres|cuatro)\s+(?:cifras?\s+)?decimal(?:es)?",                         # Tay Ban Nha
    r"(\d+|uma?|duas|dois|três)\s+casas?\s+decimais",                                         # Bo Dao Nha
    r"(\d+|eine[r]?|zwei|drei)\s+(?:nachkommastellen?|dezimalstellen?)",                     # Duc
    r"소수점\s*(?:이하\s*)?(첫째|둘째|셋째|넷째|\d+)\s*(?:번째\s*)?자리",                        # Han
    r"(?:保留|精确到|保留到)?\s*(一|两|二|三|四|\d+)\s*位小数|小数点后\s*(一|两|二|三|四|\d+)\s*位",  # Trung
    r"小数(?:点以下)?\s*(?:第)?\s*(一|二|三|四|\d+)\s*(?:位|桁)",                               # Nhat
]
_ML_INTEGER = r"nombre entier|arrondi à l'unité|número entero|número inteiro|ganze zahl|정수|整数"
_ML_BOOL = [(r"\boui\b\s*(ou|/)\s*\bnon\b", ("Oui", "Non")), (r"\bs[ií]\b\s*(o|/)\s*\bno\b", ("Sí", "No")),
            (r"\bja\b\s*(oder|/)\s*\bnein\b", ("Ja", "Nein")), (r"\bsim\b\s*(ou|/)\s*\bnão\b", ("Sim", "Não")),
            (r"是\s*(或|还是|/)\s*否", ("是", "否")), (r"예\s*(또는|/|혹은)\s*아니오", ("예", "아니오")),
            (r"はい\s*(か|または|/)\s*いいえ", ("はい", "いいえ"))]


def _part_kind(p: str):
    """Kieu cua tung phan trong mau dap an (vd `sheet name, integer value`): text / year / int / number / None."""
    p = p.lower()
    if re.search(r"\byears?\b|\bnăm\b", p) and not re.search(r"change|difference|gap|increase|decrease|count", p):
        return "year"
    if re.search(r"name|group|class|category|sector|industry|sheet|label|country|state|region|city|product|item|"
                 r"type|occupation|person|tên|nhóm|ngành", p):
        return "text"
    if re.search(r"integer|whole|count|number of", p):
        return "int"
    if re.search(r"value|number|difference|gap|increase|decrease|change|total|amount|rate|percent|share|ratio|"
                 r"sum|average|mean|score|hours|giá trị|số", p):
        return "number"
    return None


def _instruction(query: str) -> str:
    """Phan chi dan dinh dang (tu chu 'Answer'/'Provide'/'Trả lời'... cuoi cung) - de dem so gia tri."""
    q = query.lower()
    m = list(re.finditer(r"\b(answer|provide|return|respond|give|list|report|trả lời|liệt kê|đưa ra|cho biết)\b", q))
    return q[m[-1].start():] if m else ""


def parse_spec(query: str) -> dict:
    """Doc yeu cau dinh dang trong cau hoi (tieng Anh va tieng Viet)."""
    q = query.lower()
    spec = {"yesno": False, "truefalse": False, "bool_words": ("Yes", "No"), "decimals": None, "integer": False,
            "percent_sign": False, "thousands": False, "date_fmt": None, "sep": ", ", "count": None}
    if re.search(r"\byes\b['\"]?\s*(or|/)\s*['\"]?\bno\b", q):
        spec["yesno"] = True
    elif re.search(r"\btrue\b['\"]?\s*(or|/)\s*['\"]?\bfalse\b", q):
        spec["truefalse"], spec["bool_words"] = True, ("True", "False")
    elif re.search(r"\bcó\b['\"]?\s*(hoặc|hay|/)\s*['\"]?\bkhông\b", q):
        spec["yesno"], spec["bool_words"] = True, ("Có", "Không")
    elif re.search(r"\bđúng\b['\"]?\s*(hoặc|hay|/)\s*['\"]?\bsai\b", q):
        spec["truefalse"], spec["bool_words"] = True, ("Đúng", "Sai")
    else:
        for pat, words in _ML_BOOL:
            if re.search(pat, q):
                spec["yesno"], spec["bool_words"] = True, words
                break
    m = re.search(_NUM_WORD + r"\s+decimal", q) or re.search(r"\b(\d+)\s*(d\.p\.|dp)\b", q)
    m2 = re.search(r"nearest (tenth|hundredth|thousandth)", q)
    m_vi = re.search(r"(\d+|một|hai|ba|bốn)\s+chữ số\s+(thập phân|sau dấu (phẩy|chấm))", q)
    m2_vi = re.search(r"hàng phần (mười|trăm|nghìn|ngàn)", q)
    m_ml = next((mm for mm in (re.search(p, q) for p in _ML_DECIMALS) if mm), None)
    if m_ml and not (m or m2 or m_vi or m2_vi):
        g = next(x for x in m_ml.groups() if x)
        spec["decimals"] = int(g) if g.isdigit() else _ML_NUM.get(g)
    elif m:
        g = m.group(1)
        spec["decimals"] = int(g) if g.isdigit() else _WORD_NUM[g]
    elif m2:
        spec["decimals"] = _PLACES[m2.group(1)]
    elif m_vi:
        g = m_vi.group(1)
        spec["decimals"] = int(g) if g.isdigit() else _VI_NUM[g]
    elif m2_vi:
        spec["decimals"] = _VI_PLACES[m2_vi.group(1)]
    elif spec["decimals"] is None and re.search(
            r"\b(an |the |only the |two |three )?integers?\b|whole numbers?|no decimal|without decimal"
            r"|nearest (unit|one)\b|số nguyên|hàng đơn vị|không (lấy|có|ghi) (phần|chữ số) thập phân|" + _ML_INTEGER, q):
        spec["integer"] = True
    if spec["decimals"] == 0:
        spec["decimals"], spec["integer"] = None, True
    pct = r"(%|percent(age)? (sign|symbol))"
    if re.search(pct, q) and not re.search(r"\b(without|no|omit|exclude|excluding|not include|don't include|không|bỏ)"
                                           r"\b[^.;]{0,30}" + pct, q):
        if re.search(r"\b(with|include|including|followed by|add|append|as|kèm|thêm|có|bao gồm|ghi)\b[^.;]{0,30}"
                     + pct, q) or re.search(r"e\.g\.?,?\s*['\"`]?-?[\d.]+%|ví dụ:?\s*['\"`]?-?[\d.,]+%", q):
            spec["percent_sign"] = True
    if (re.search(r"thousands?[- ]separators?|commas? (as|for) (the )?thousands?", q)
            and not re.search(r"\b(without|no|omit|exclude)\b[^.;]{0,25}(thousands?[- ]separators?|commas)", q)) \
            or (re.search(r"phân cách hàng (nghìn|ngàn)", q)
                and not re.search(r"không[^.;]{0,25}phân cách hàng (nghìn|ngàn)", q)):
        spec["thousands"] = True
    for pat, fmt in _DATE_FORMATS:
        if re.search(pat, q):
            spec["date_fmt"] = fmt
            break
    instr = _instruction(query)
    if re.search(r"separated by (a )?semicolons?|semicolon[- ]separated|chấm phẩy", q):
        spec["sep"] = "; "
    elif re.search(r"comma(s)? (and )?(with )?no spaces?|comma without (a )?spaces?", q):
        spec["sep"] = ","
    m = re.search(r"\b" + _NUM_WORD + r"\s+((?!decimal)\w+\s+){0,3}?(integers|numbers|values|names|categories|"
                  r"items|words|labels|answers|percentages|rates|dates|years|amounts|counts|industries|sectors|"
                  r"occupations|groups|countries|regions|products|people|persons|states|cities|months)\b", instr)
    m_vi = re.search(r"\b(hai|ba|bốn|\d+)\s+((?!chữ)\w+\s+){0,2}?(giá trị|con số|số|tên|ngành|nhóm|mục|đáp án)\b",
                     instr)
    if m and m.group(1) not in ("zero", "no", "one"):
        spec["count"] = int(m.group(1)) if m.group(1).isdigit() else _WORD_NUM[m.group(1)]
    elif m_vi:
        spec["count"] = int(m_vi.group(1)) if m_vi.group(1).isdigit() else _VI_NUM[m_vi.group(1)]
    tpl = re.search(r"[`'\"]([^`'\"]{1,60},[^`'\"]{1,60})[`'\"]", instr)       # vd "Answer as `men, women`"
    if spec["count"] is None and tpl:
        spec["count"] = tpl.group(1).count(",") + 1
    # kieu tung phan: `group, difference` -> [text, number]; `year, decrease` -> [year, number]
    spec["parts"] = [_part_kind(p) for p in tpl.group(1).split(",")] if tpl else []
    # cau hoi hoi NAM -> ket qua phai giong mot nam (chan loi tra ve so dong Excel tu idxmax)
    spec["asks_year"] = bool(re.search(
        r"\b(which|what|identify the|in which|find the) year\b|\byear (when|in which)\b|năm nào|quelle année|"
        r"en quelle année|qué año|en qué año|in welchem jahr|哪一年|哪年|어느 해|몇 년도", q))
    # "giam bao nhieu / thap hon bao nhieu" -> tra ve do lon (so duong), tru khi cau hoi doi giu dau
    spec["magnitude"] = bool(re.search(
        r"\b(decrease[sd]?|decline[sd]?|drop(ped)?|fell|fall|lower|less|reduc\w*|shr[iu]nk)\b|감소|하락|낮|下降|减少|降低|"
        r"低多少|giảm|thấp hơn|ít hơn|baiss|diminu|inférieur|disminu|rückgang|gesunken|weniger", q)) and not re.search(
        r"\b(minus|negative|signed|sign|change from|percent change|net change)\b|keep the sign|âm|négati|negativ", q)
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
        yes, no = spec["bool_words"]          # Yes/No, True/False, Có/Không, Đúng/Sai theo cau hoi
        return yes if v else no
    if isinstance(v, (int, float)):
        if (spec["yesno"] or spec["truefalse"]) and v in (0, 1):
            return _fmt_one(bool(v), spec)
        return _fmt_number(v, spec)
    if isinstance(v, (dt.datetime, dt.date)):
        return _fmt_date(v, spec)
    s = str(v).strip()
    if spec["yesno"] or spec["truefalse"]:
        low = s.lower().strip(".! ")
        if low in ("yes", "true", "y", "có", "co", "đúng", "dung", spec["bool_words"][0].lower()):
            return _fmt_one(True, spec)
        if low in ("no", "false", "n", "không", "khong", "sai", spec["bool_words"][1].lower()):
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
    return re.sub(r"[^\w\s]", " ", s.lower()).split()


def question_candidates(query: str) -> list:
    """Cac ten ma cau hoi liet ke san (vd 'Rank ...: Agriculture, Manufacturing, and Construction' hoac '...'
    trong ngoac nhay) - dap an dang ten phai viet dung nhu cau hoi."""
    m = re.search(r"\b(answer|provide|return|respond|trả lời)\b", query, re.I)
    body = query[:m.start()] if m else query
    cands = [a or b for a, b in re.findall(r"(?<!\w)'([^']{2,80})'(?!\w)|\"([^\"]{2,80})\"", body)]
    m2 = re.search(r":\s*([^:?]+?)\s*[?.]?\s*$", body.strip())
    if m2:
        # "A, B, and C" / "A, B or C" / "A and B": tach theo dau phay; chi tach and/or o vi tri CUOI
        # (khong tach "Bachelor's degree and higher" o giua ten)
        parts = [p.strip() for p in m2.group(1).split(",")]
        last = parts[-1]
        lead = re.match(r"(and|or|và|hoặc)\s+", last)
        if lead:
            parts[-1] = last[lead.end():]
        else:
            sep = list(re.finditer(r"\s+(or|hoặc)\s+", last)) or \
                (list(re.finditer(r"\s+(and|và)\s+", last)) if len(parts) == 1 else [])
            if sep:
                parts[-1:] = [last[:sep[-1].start()], last[sep[-1].end():]]
        parts = [re.sub(r"\s*\([^)]*\)", "", p).strip(" .?'\"") for p in parts]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            cands += parts
    return cands


def align_to_candidates(s: str, cands: list) -> str:
    """Nhan trong bang -> ten trong cau hoi khi cau hoi liet ke san:
    'Agriculture and related' + ung vien 'Agriculture' -> 'Agriculture'. Chi doi khi khop duy nhat."""
    w = _words(s)
    if not w or any(_words(c) == w for c in cands):
        return s
    hits = {c for c in cands if _words(c) and len(" ".join(_words(c))) >= 3 and w[:len(_words(c))] == _words(c)}
    return hits.pop() if len(hits) == 1 else s


def _fmt_part(v, kind, spec) -> str:
    """Dinh dang 1 phan theo kieu trong mau dap an: nam/so nguyen khong co phan thap phan; ten giu nguyen chu."""
    if kind in ("year", "int") and isinstance(v, (int, float)) and not isinstance(v, bool):
        return _fmt_number(v, {**spec, "decimals": None, "integer": True, "percent_sign": False,
                               "thousands": spec["thousands"] and kind == "int"})
    if kind == "text" and isinstance(v, str):
        return clean_text(v)
    return _fmt_one(v, spec)


def format_answer(result, query: str) -> str:
    spec = parse_spec(query)
    values = result if isinstance(result, list) else [result]
    cands = question_candidates(query)
    if cands:
        values = [align_to_candidates(v, cands) if isinstance(v, str) else v for v in values]
    values = [_plain(v) for v in values]
    parts = spec["parts"] if len(spec["parts"]) == len(values) else [None] * len(values)
    if spec["magnitude"]:
        values = [abs(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "year" else v
                  for v, k in zip(values, parts)]
    return spec["sep"].join(_fmt_part(v, k, spec) for v, k in zip(values, parts))


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
        if any(spec["parts"]):
            names = {"text": "text", "year": "a year number", "int": "a number", "number": "a number (unrounded)"}
            desc = ", ".join(names.get(k, "a value") for k in spec["parts"])
            return f"a list of {spec['count']} values in the requested order: [{desc}]"
        each = f", each {kind}" if kind else ""
        return f"a list of {spec['count']} values in the requested order{each}"
    return kind or ""


def type_problem(result, query: str) -> str | None:
    """Kiem tra ket qua co dung kieu/so luong ma cau hoi yeu cau; tra ve mo ta loi (de LLM sua) hoac None."""
    spec = parse_spec(query)
    values = result if isinstance(result, list) else [result]
    if spec["count"] and spec["count"] > 1 and len(values) != spec["count"]:
        return f"the question asks for {spec['count']} values but `result` has {len(values)}"
    parts = spec["parts"] if len(spec["parts"]) == len(values) else [None] * len(values)
    if spec["asks_year"] and not any(parts) and len(values) == 1:
        parts = ["year"]
    for v, kind in zip(values, parts):
        v = _plain(v)
        if kind == "year":
            y = _to_number(v) if isinstance(v, str) else v
            if isinstance(y, (int, float)) and not isinstance(y, bool) and not 1000 <= y <= 2200:
                return (f"the question asks for a YEAR but `result` has {v!r}, which looks like a row index "
                        f"(df.index / idxmax() give the Excel row number); read the year from that row instead")
            continue
        if kind == "text":                     # vd ten sheet / ten nhom: phai la chu, khong phai so thu tu
            if not isinstance(v, str):
                return f"the answer part for a name must be text (e.g. a sheet or group name), not {v!r}"
            continue
        if spec["yesno"] or spec["truefalse"]:
            if isinstance(v, bool) or (isinstance(v, (int, float)) and v in (0, 1)):
                continue
            if isinstance(v, str) and v.strip().lower().strip(".! ") in (
                    "yes", "no", "true", "false", "có", "không", "đúng", "sai",
                    *(w.lower() for w in spec["bool_words"])):
                continue
            return f"the question asks for Yes/No, so `result` must be a bool, not {v!r}"
        if spec["decimals"] is not None or spec["integer"]:
            if isinstance(v, bool) or not (isinstance(v, (int, float)) or
                                           (isinstance(v, str) and _to_number(v) is not None)):
                return f"the question asks for a number, but `result` contains {v!r}"
    return None
