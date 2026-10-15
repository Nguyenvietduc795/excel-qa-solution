"""Buoc 2 - Goi LLM qua OpenRouter (mac dinh qwen/qwen3.7-flash) de sinh code pandas."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import requests

DEFAULT_MODEL = "qwen/qwen3.7-flash"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You answer questions about spreadsheet tables by writing Python (pandas) code.

The tables are shown as text. In the code these variables already exist:
- `tables`: list of pandas DataFrames, one per table shown (tables[0], tables[1], ...). `df` = tables[0].
- Each DataFrame index = Excel row number (the number before ':' in the text).
- Columns = Excel column letters ('A', 'B', ...) as listed under "Columns", plus 'section'
  (the section heading(s) the row belongs to, e.g. 'TOTAL', 'Women', 'MARITAL STATUS > Men, 16 years and over').
  Always access columns by LETTER: df['E'], df.loc[31, 'E']. The text after "E =" is only a label; df['Date of | Birth']
  or df['Score'] raises KeyError.
- Numbers are numeric, dates are pandas Timestamps, empty cells are NaN. `pd`, `np`, `math`, `re`, `datetime` are available.
- `col_name(letter, table=0)` returns the clean display name of a column (the most specific header part),
  e.g. col_name('K') -> 'Leisure and hospitality'. Use it when the answer is a column (never return a bare letter).

Rules:
1. First find the exact table, rows and columns the question refers to by reading the table text, then write
   short comments naming them (e.g. `# row 31 = Women / Unemployment rate; columns B..I = education levels`).
   Hard-coding Excel row numbers and column letters you identified is encouraged: df.loc[31, 'E'].
   If several tables are shown (other sheets/blocks), pick the one whose title/columns match the question.
2. The same row label often appears in several sections/blocks (TOTAL, Men, Women, races...). Use the block the
   question asks about; if it names no group, use the overall/total block (usually the first one).
   Rows can also be nested WITHOUT markers: a general row ('Men, 16 years and over', 'Manufacturing',
   '1 to 34 hours') is followed by its sub-rows ('16 to 19 years', 'Durable goods', '1 to 4 hours'), so the same
   sub-row label belongs to different parents; pick the one under the right parent.
3. Column names may be hierarchical ("Parent | Child"). Match the question's wording to the most specific column.
   "Across all X" / "average across all X" means every column (or row) of that kind as listed, unless the
   question says otherwise.
4. Totals: if the table already has a total row/column for what is asked ('Total', 'All industries',
   'Total, 16 years and over'), use that value instead of adding sub-rows (sub-rows may overlap). Add values only
   when no such total exists. For "which ... highest/lowest" questions, skip the overall total row/column itself
   but keep every other listed row/column (including subtotals), unless the question restricts the set.
   For "how many" questions over a list of records, count the matching rows (excluding total/summary rows).
5. Arithmetic: do it in code. "difference" = larger - smaller unless a direction is asked ("how much did X
   increase" = new - old); "percentage/share of" = part / whole * 100; "percent change" = (new - old) / old * 100.
   Never round, never format, never add units: formatting is done later.
   Use values exactly as shown: never multiply/convert because of "[Numbers in thousands]" or similar notes.
6. Text matching: compare labels case-insensitively after stripping spaces; the question may paraphrase labels
   (plural/singular, '&' vs 'and', small wording changes) or be written in Vietnamese.
7. Put the final answer in a variable named `result`:
   - a number, a string, a bool (for Yes/No questions), a pd.Timestamp for a date, or a list for multi-part
     answers (in the requested order).
   - For a name/category answer return the label text exactly as it appears in the table, EXCEPT when the
     question itself lists the candidate names (e.g. "rank Agriculture, Manufacturing, and Construction"):
     then return the names spelled exactly as in the question.
   - For people, return only the part asked for (e.g. the last name column).
   - If several items tie, return the first in table order unless the question asks for all of them.
8. Dates: age on a date = time between birth date and that date (use exact dates: (date - birth).dt.days).
   If the table has a birth date AND an event date (admission, hire, enrollment...), "young/old/age" questions
   refer to the age on that event date for each person, even when the question does not repeat it.
   So "oldest/youngest" = largest/smallest (event date - birth date), NOT simply the earliest/latest birth date.
9. Reply with ONE ```python code block only, no explanation."""

USER_TEMPLATE = """{tables}
Question: {question}{hint}"""

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
                 temperature=0.0, max_tokens=1500, timeout=60, http_retries=3):
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


def build_messages(tables_text: str, question: str, hint: str = ""):
    hint = f"\n(`result` should be {hint}.)" if hint else ""
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(tables=tables_text, question=question, hint=hint)}]


def build_direct_messages(tables_text: str, question: str):
    """Du phong khi code that bai het: hoi LLM tra loi truc tiep."""
    return [{"role": "system", "content": DIRECT_PROMPT},
            {"role": "user", "content": f"{tables_text}\nQuestion: {question}"}]
