"""Buoc 2 - Goi LLM qua OpenRouter (mac dinh qwen/qwen3.7-flash) de sinh code pandas."""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pandas as pd
import requests

DEFAULT_MODEL = "qwen/qwen3.7-flash"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You answer questions about spreadsheet tables by writing Python (pandas) code.

In the code these variables already exist:
- `tables`: list of DataFrames, one per table shown (tables[0], tables[1], ...); `df` = tables[0];
  `sheets[i]` = the sheet name (text) of tables[i].
- Index = Excel row number (the number before ':'). Columns = Excel letters listed under "Columns", plus
  'section' (the group heading the row belongs to, e.g. 'Women' or 'MARITAL STATUS > Men, 16 years and over').
- Numbers are numeric, dates are pd.Timestamp, empty cells are NaN. `pd`, `np`, `math`, `re`, `datetime` exist.
- Access columns ONLY by their letter (df['F'], df.loc[31, 'E']), never by header text.
  `col_name(letter, table=0)` gives a column's display name; use it ONLY when the answer is a column name.

Rules:
1. "Notes for this question" (below the question, when present) are facts about THIS table and question.
   Follow them exactly; they override your own habits.
2. Find the exact table, rows and columns first and name them in a short comment
   (e.g. `# row 31 = Women / Unemployment rate; columns B..I`). Hard-coding them is fine: df.loc[31, 'E'].
3. A label can repeat in several blocks (TOTAL, Men, Women...) or under different parent rows: use the
   block/parent the question names; if it names none, use the overall (first) block.
4. Do every calculation in code. Never round, format or add units to `result`. Note: idxmax()/idxmin() return the
   Excel ROW NUMBER; to answer with a name or year, read it from that row (e.g. df.loc[df['G'].idxmax(), 'B']).
5. `result` = a number, a string, a bool (Yes/No questions), a pd.Timestamp, or a list (multi-part answers, in
   the requested order). Names: the label exactly as in the table, or as spelled in the question when the
   question lists the candidates. For people return only the part asked for. Ties: keep table order
   (sort_values(..., kind='stable')).
6. Reply with ONE ```python code block only, no explanation."""

USER_TEMPLATE = """{tables}
Question: {question}{notes}{hint}"""

DIRECT_PROMPT = """You answer questions about spreadsheet tables. Read the tables carefully and compute the answer.
Reply with ONLY the final answer on one line (no explanation, no units), formatted exactly as the question asks."""

_lock = threading.Lock()


def load_dotenv(*paths):
    """Doc file .env don gian (KEY=VALUE), khong ghi de bien moi truong da co."""
    for p in paths:
        p = Path(p)
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, model=None, api_key=None, base_url=None, reasoning="off",
                 temperature=0.0, max_tokens=3000, timeout=60, http_retries=3):
        self.model = model or os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.base_url = (base_url or os.getenv("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.reasoning = reasoning
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.http_retries = http_retries
        if not self.api_key:
            raise LLMError("Missing API key: set OPENROUTER_API_KEY (environment variable or .env file)")
        self.session = requests.Session()
        # 20 cau x 2 loi giai goi API cung luc -> tang so ket noi giu san (mac dinh chi 10)
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=64)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def chat(self, messages, temperature=None) -> tuple[str, dict]:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            "usage": {"include": True},
        }
        if self.reasoning == "off":
            body["reasoning"] = {"enabled": False}
        elif self.reasoning in ("low", "medium", "high"):
            body["reasoning"] = {"effort": self.reasoning}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                   "X-Title": "excel-qa"}
        last = None
        for attempt in range(self.http_retries + 1):
            try:
                r = self.session.post(f"{self.base_url}/chat/completions", json=body,
                                      headers=headers, timeout=self.timeout)
                if r.status_code == 400 and "reasoning" in body:
                    body.pop("reasoning")        # model khong ho tro tham so reasoning
                    continue
                if r.status_code in (408, 429) or r.status_code >= 500:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if r.status_code != 200:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                data = r.json()
                if "error" in data:
                    last = str(data["error"])[:300]
                    time.sleep(min(2 ** attempt, 8))
                    continue
                text = data["choices"][0]["message"].get("content") or ""
                u = data.get("usage") or {}
                usage = {"prompt_tokens": u.get("prompt_tokens", 0),
                         "completion_tokens": u.get("completion_tokens", 0),
                         "total_tokens": u.get("total_tokens", 0),
                         "cost": float(u.get("cost") or 0.0)}
                return text, usage
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(min(2 ** attempt, 8))
        raise LLMError(f"LLM request failed after retries: {last}")


def build_messages(tables_text: str, question: str, hint: str = "", notes: list | None = None):
    hint = f"\n(`result` should be {hint}.)" if hint else ""
    notes = "\nNotes for this question:\n" + "\n".join(f"- {n}" for n in notes) if notes else ""
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(tables=tables_text, question=question, notes=notes,
                                                             hint=hint)}]


# ----------------------------------------------------------------------------
# Ghi chu rieng cho tung cau (sinh bang Python, khong dung LLM): chi them quy tac khi cau hoi + bang can den,
# dat ngay duoi cau hoi va ghi dich danh cot/dong -> LLM khong bo qua (tranh "prompt bi loang").
_VI_CHARS = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", re.I)
_AGE = r"\b(young\w*|old(er|est)?|ages?|aged|born|birth\w*|tuổi|trẻ|già)\b"
_AVG = r"\b(average|mean)\b[^.?]*\b(across|all|every)\b|trung bình[^.?]*(tất cả|mọi|các)"
_TOTAL = r"\b(total|sum|combined|altogether|overall)\b|tổng"
_SUPER = (r"\b(highest|lowest|largest|smallest|most|least|maximum|minimum|top|biggest|rank\w*)\b"
          r"|cao nhất|thấp nhất|lớn nhất|nhỏ nhất|nhiều nhất|ít nhất")
_TOTAL_LABEL = re.compile(r"\btotal\b|\ball industries\b|\btổng\b", re.I)
_TOTAL_ROW = re.compile(r"^(total|all\b|tổng)", re.I)


def _ref(k: int) -> str:
    return "df" if k == 0 else f"tables[{k}]"


def _age_note(tables, q: str = "") -> str | None:
    # chieu sap xep noi thang theo tu trong cau hoi (LLM hay nham "gia nhat = tuoi nho nhat")
    if re.search(r"\bold(er|est)?\b|già", q):
        way = "OLDEST/OLDER = LARGEST age_days -> sort_values('age_days', ascending=False). "
    elif re.search(r"\byoung(er|est)?\b|trẻ", q):
        way = "YOUNGEST/YOUNGER = SMALLEST age_days -> sort_values('age_days', ascending=True). "
    else:
        way = ""
    for k, t in enumerate(tables):
        dcols = [c for c in t.columns if pd.api.types.is_datetime64_any_dtype(t.df[c])]
        birth = [c for c in dcols if re.search(r"birth|born|dob|sinh", t.columns[c], re.I)]
        events = [c for c in dcols if c not in birth[:1]]
        if not birth or not events:
            continue
        b, e, r = birth[0], events[0], _ref(k)
        evs = ", ".join(f"{c} ('{t.columns[c]}')" for c in events)
        return (f"{t.name} has a birth date column {b} ('{t.columns[b]}') and event date column(s) {evs}. "
                f"'Young/old/age' means each person's age ON THE EVENT DATE, even if the question does not "
                f"repeat it: age_days = ({r}['{e}'] - {r}['{b}']).dt.days. {way}"
                f"Do NOT rank by birth date alone."
                + (" If several event dates exist, use the one the question names." if len(events) > 1 else "")
                + " Whole years on date d: d.year - b.year - ((d.month, d.day) < (b.month, b.day)).")
    return None


def _label_col(t):
    for c in t.columns:
        if t.df[c].dtype == object or pd.api.types.is_string_dtype(t.df[c]):
            if t.df[c].notna().sum() >= max(1, len(t.df) // 2):
                return c
    return None


def _has_total(tables, rows_only=False) -> bool:
    """Bang co dong (hoac cot) tong? rows_only: chi xet nhan dong bat dau bang Total/All (cho cau cao nhat/thap nhat)."""
    for t in tables:
        if not rows_only and any(_TOTAL_LABEL.search(n) for n in t.columns.values()):
            return True
        c = _label_col(t)
        pat = _TOTAL_ROW if rows_only else _TOTAL_LABEL
        if c and t.df[c].dropna().astype(str).map(lambda s: bool(pat.search(s.strip()))).any():
            return True
    return False


def _repeat_note(tables, q: str) -> str | None:
    """Nhan dong lap lai trong cung khoi (vd '16 to 19 years' duoi Men/Women) ma cau hoi co nhac den."""
    for k, t in enumerate(tables):
        c = _label_col(t)
        if c is None:
            continue
        labels = t.df[c].astype(str).str.strip()
        sections = t.df["section"].fillna("").astype(str)
        groups = sorted(labels.groupby([sections, labels]), key=lambda kv: kv[1].index.min())
        for (sec, lab), grp in groups:                      # theo thu tu trong bang (khoi tong truoc)
            if len(grp) < 2 or lab in ("nan", ""):
                continue
            words = lab.lower().split()
            if len(words) < 2:                   # 'Men', 'Women'... lap o qua nhieu ngu canh -> de goi y sai
                continue
            core = " ".join(words[:-1]) if len(words) >= 3 else lab.lower()
            if not re.search(r"(?<!\w)" + re.escape(core) + r"(?!\w)", q.lower()):
                continue
            in_sec = labels[sections == sec]
            counts = in_sec.value_counts()
            occ, first_parent = [], ""
            for r in list(grp.index)[:6]:
                parents = [i for i in in_sec.index if i < r and counts.get(in_sec[i], 0) == 1]
                first_parent = first_parent or (in_sec[parents[-1]] if parents else "")
                occ.append(f"row {r}" + (f" (under '{in_sec[parents[-1]]}')" if parents else ""))
            default = " (none named: the first one)" if _TOTAL_ROW.search(first_parent) else ""
            return (f"In {t.name}, the label '{lab}' repeats: {'; '.join(occ)}. Each occurrence belongs to the "
                    f"general row above its group; use the one under the parent the question names{default}.")
    return None


_STOP = {"the", "and", "for", "who", "with", "all", "of", "in", "at", "by", "or", "to", "from", "per", "total"}


def _tokens(s: str) -> set:
    return {w for w in re.findall(r"\w+", s.lower()) if (len(w) >= 3 or w.isdigit()) and w not in _STOP}


def _parent_column_note(tables, q: str) -> str | None:
    """Cau hoi chi neu (trong ngoac kep) ten cot cha, vd "Average hours", ma cot cha co nhieu cot con va cau hoi
    khong nhac tu nao cua cot con -> dung cot con dau tien (thuong la Total)."""
    quoted = [a or b or c or d for a, b, c, d in
              re.findall(r"\"([^\"]+)\"|“([^”]+)”|`([^`]+)`|(?<!\w)'([^']+)'(?!\w)", q)]
    if not quoted:
        return None
    for k, t in enumerate(tables):
        groups = {}
        for letter, name in t.columns.items():
            parts = name.split(" | ")
            if len(parts) >= 2:
                groups.setdefault(" | ".join(parts[:-1]), []).append((letter, parts[-1]))
        for parent, kids in groups.items():
            pname = parent.split(" | ")[-1].lower()
            if len(kids) < 2 or len(pname) < 4 or not any(pname == x.strip().lower() for x in quoted):
                continue
            q_tok = _tokens(q) - _tokens(parent)
            if any(_tokens(c) & q_tok for _, c in kids):
                continue                       # cau hoi da nhac den cot con (vd ngay thang, 'full time')
            kl = ", ".join(f"{L} ('{c}')" for L, c in kids[:5])
            return (f"In {t.name}, '{parent}' has sub-columns {kl}. The question names only '{parent}', so use its "
                    f"first sub-column {kids[0][0]} ('{kids[0][1]}') unless the question implies another one.")
    return None


def _grouped_note(tables) -> str | None:
    """Bang chia nhom bang tieu de section, moi nhom lap lai cung bo nhan dong (vd moi nganh co cac dong
    'Total all sizes', '1 - 10', '250 - 999'): noi ro ten nhom nam o cot 'section', khong phai cot nhan."""
    for t in tables:
        c = _label_col(t)
        if c is None:
            continue
        sec = t.df["section"].fillna("").astype(str)
        if sec.nunique() < 3:
            continue
        labels = t.df[c].astype(str).str.strip()
        spread = labels.groupby(labels).apply(lambda s: sec[s.index].nunique())
        common = [lab for lab, k in spread.items() if k >= 3]
        if len(common) < 3:
            continue
        ex = ", ".join(f"'{x}'" for x in common[:4])
        groups = list(dict.fromkeys(s.split(" > ")[-1] for s in sec if s))
        gx = ", ".join(f"'{x}'" for x in groups[:4])
        return (f"In {t.name}, ROWS are grouped under section headings (column 'section', last part after ' > ': "
                f"{gx}...), and the row labels in column {c} ({ex}...) repeat inside every group. If the question asks "
                f"which of these row groups (e.g. which industry), answer with the section heading text; if it asks "
                f"for one of the repeated row labels (e.g. a size class), answer with the column {c} label. Groups "
                f"named in column headers are columns, not sections.")
    return None


def question_notes(tables, question: str) -> list[str]:
    """Cac ghi chu ngan, cu the cho cau hoi nay (rong neu khong can)."""
    q = question.lower()
    # cau hoi bo cac ten cot (vd 'total at work') -> chu 'total' trong ten cot khong kich hoat ghi chu dong tong
    q_wo_cols = q
    for t in tables:
        for name in t.columns.values():
            part = name.split(" | ")[-1].lower()
            if len(part) > 5 and _TOTAL_LABEL.search(part):
                q_wo_cols = q_wo_cols.replace(part, " ")
    notes = []
    if len(tables) > 1:
        desc = "; ".join(f"{t.name} = sheet '{t.sheet}'" + (f" ({t.title[:60]})" if t.title else "") for t in tables)
        notes.append(f"Several tables are shown: {desc}. Use the one(s) the question is about; if it needs data "
                     f"from two tables, combine them (e.g. match rows by name).")
    if re.search(r"\bsheets?\b|\bworksheets?\b|trang tính|feuille|hoja|시트|工作表", q):
        names = ", ".join(f"sheets[{i}] = '{t.sheet}'" for i, t in enumerate(tables))
        notes.append(f"When the answer is a sheet, return its NAME as text exactly as written ({names}), "
                     f"never the table index number.")
    if re.search(_AGE, q):
        n = _age_note(tables, q)
        if n:
            notes.append(n)
    if re.search(_AVG, q):
        notes.append("Organizer convention: an average 'across all ...' is the plain (unweighted) mean of EVERY "
                     "value of that row/column as listed, INCLUDING subtotal and 'Total' columns, unless the question "
                     "says to exclude totals or use subcategories only.")
    elif re.search(_TOTAL, q_wo_cols) and _has_total(tables):
        notes.append("If the table already has a row/column that IS the total asked for (e.g. 'Total', "
                     "'All industries', 'Total, 16 years and over'), read it directly instead of adding sub-rows "
                     "(sub-rows can overlap). Add values only when no such total exists or the question names the "
                     "items to add.")
    if re.search(_SUPER, q) and _has_total(tables, rows_only=True):
        notes.append("For highest/lowest/most/least: exclude only the overall total row/column itself; keep every "
                     "other listed row/column (subtotals included) unless the question restricts the set.")
    col_names = " ".join(n.lower() for t in tables for n in t.columns.values())
    if re.search(r"percent(age)? change|growth|grew|tăng trưởng", q) and "percent change" not in col_names:
        notes.append("Percent change from A to B = (B - A) / A * 100; 'change from A to B' = B - A (keep the sign).")
    elif re.search(r"\b(difference|gap)\b|chênh lệch", q):
        notes.append("'Difference between X and Y' = absolute difference unless the question gives a direction "
                     "('how much more', 'X minus Y', 'change from A to B' = B - A).")
    if re.search(r"\b(share|proportion|percentage of|percent of|what percent)\b|tỷ lệ|tỉ lệ", q):
        notes.append("Share = part / whole * 100 (if the table already shows this percentage, read it instead).")
    for k, t in enumerate(tables):
        # cot do sau phan cap: ten 'Indent Level'/'Level' va gia tri la so nguyen nho (khong phai 'Level 1' tai chinh)
        lvl = [c for c, n in t.columns.items()
               if (re.search(r"\bindent\b", n, re.I) or n.strip().lower() in ("level", "cấp", "niveau", "nivel"))
               and pd.api.types.is_numeric_dtype(t.df[c]) and t.df[c].dropna().between(0, 10).all()
               and (t.df[c].dropna() % 1 == 0).all()]
        if lvl:
            notes.append(f"In {t.name}, column {lvl[0]} ('{t.columns[lvl[0]]}') is the hierarchy depth: the items "
                         f"under a category are the rows right after it with a larger value, until the value drops "
                         f"back to the category's level or less.")
            break
    sub = _parent_column_note(tables, q)
    if sub:
        notes.append(sub)
    grouped = _grouped_note(tables)
    if grouped:
        notes.append(grouped)
    rep = None if grouped else _repeat_note(tables, question)
    if rep:
        notes.append(rep)
    if re.search(r"[^\x00-\x7f]", re.sub(r"[“”‘’–—…]", "", question)) and \
            sum(ch.isalpha() and ord(ch) > 127 for ch in question) >= 3:
        notes.append("The question is not in English: match it to the table labels by meaning (labels may be in "
                     "another language) and return labels exactly as written in the table.")
    return notes


def build_direct_messages(tables_text: str, question: str):
    """Du phong khi code that bai het: hoi LLM tra loi truc tiep."""
    return [{"role": "system", "content": DIRECT_PROMPT},
            {"role": "user", "content": f"{tables_text}\nQuestion: {question}"}]
