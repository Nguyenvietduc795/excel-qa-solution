# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A competition solution (Excel QA, "chủ đề 2") that answers natural-language questions over Excel files and writes `submission.csv` (`id,answer`). The rules are in `../../ĐỀ THI/de_thi_chu_de_2.docx`:
- The model must be `qwen/qwen3.7-flash` via OpenRouter.
- Cost is capped at 0.10 USD per question, retries included.
- 20 questions must finish in 40 minutes on 6 vCPU, using at most 1 GB RAM.
- Ranking is by Exact Match count, then average time per question, then average tokens per question.
- The hidden test set is built the same way as the public data. A second public batch is due 16/10. The submission deadline is 17/10 12:00, followed by an oral defence where the team must explain and modify the code.

Code comments and the README are in Vietnamese without diacritics.

## Layout on disk

The sample data sits two levels up: `../../xlsx_files/xlsx_files/*.xlsx`, `../../queries.json` (`{id, file_name, query}`) and `../../sample_submission.csv` (gold). `find_file` searches recursively, so `--xlsx_dir ../../xlsx_files` works. `run_test.bat` looks in both `..\` and `..\..\`.

On this machine, `python` is the Microsoft Store stub. Use `py` (Python 3.14, pandas 3.x).

## Commands

```bash
py -m pip install -r requirements.txt
py main.py --xlsx_dir ../../xlsx_files --queries ../../queries.json --output submission.csv --log_dir logs
py evaluate.py --pred submission.csv --gold ../../sample_submission.csv --queries ../../queries.json
py table_parser.py ../../xlsx_files/xlsx_files/p4_t04.xlsx      # exactly what the LLM sees; no API call
```

`main.py` needs `OPENROUTER_API_KEY` in the environment or in `.env` next to `main.py` or in the cwd.

There is no test suite in the repo. To iterate on one question, run `main.py` on a `queries.json` that holds just that entry, with `--log_dir`, then read `logs/<id>.json`. It contains each attempt's chain, code, result, error or warning, the votes, any `direct_answer`, tokens, cost and time. To test without the API, replace `main.LLMClient` with a fake class whose `chat(messages, temperature)` returns ```` ```python ...``` ```` plus a `{"total_tokens", "cost"}` dict, then call `main.main([...])`.

Main `main.py` flags: `--votes 1` (default: one temperature-0 chain, a 0.6 chain only if it fails; `3` = the old voting, which let 0.6 chains outvote a correct temperature-0 answer), `--max_retries 2`, `--max_cost 0.08`, `--reasoning off|low|medium|high|default`, `--workers 8`, `--model`, `--no_direct_fallback`, `--exec_timeout`, `--question_timeout`.

## Architecture: a 4-stage pipeline per question

The design principle: **the LLM only maps the question to cells and columns by writing pandas code. All arithmetic and all answer formatting are deterministic Python**, because Exact Match punishes any formatting drift. Keep that split when you change things.

1. **`table_parser.py`** (no LLM). Turns each workbook into `Table` objects (`df`, `columns`, `title`, `captions`, `notes`, `lines`).
   - **Key convention: the DataFrame index is the Excel row number, and its columns are Excel column letters**, plus `section`. `Table.columns` maps each letter to its hierarchical name, joined as `"Parent | Child"`.
   - Pipeline: `_load_grid` reads values, merges and bold flags. `_column_blocks` splits side-by-side tables at fully empty columns. `_parse_block` classifies each row (`_row_kind`: blank, data, label or header) and finds header blocks. `_consume_table` builds the df.
   - Label-only rows become the `section` value. An uppercase label is the root, and a nested label follows ` > `.
   - Text-only tables use `_header_depth` (bold or merged leading rows) so data rows are not swallowed into the header.
   - Numeric strings (`"1,234"`, `"12%"`) count as data via `num_from_str`. Vertical merges in data rows are filled into every row.
   - `_fill_formulas` evaluates simple formulas that have no cached value.
   - `render_tables` produces the prompt text. Tables longer than `MAX_RENDER_ROWS` show only `head_rows` plus a `_profile` column summary.
   - Any change must leave `py table_parser.py` output for the 10 sample files byte-identical, unless the change is intended.
2. **`llm.py`**. `SYSTEM_PROMPT` defines the contract for the generated code. It has `tables`, `df`, `pd`, `np`, `math`, `re`, `datetime` and `col_name(letter, table=0)` available, and must set an unrounded `result`. The prompt's rules (sections, nested sub-rows, prefer existing Total rows, the difference and percent formulas) were derived from the gold answers. `build_messages` appends a `result` type hint taken from `formatter.result_hint`. `DIRECT_PROMPT` / `build_direct_messages` are the no-code fallback. `LLMClient` retries 408, 429 and 5xx, and drops `reasoning` after a 400.
3. **`executor.py`**. `run_code` `exec`s the code with restricted builtins and an import whitelist (`_ALLOWED_MODULES`), under a timeout. It normalizes the result (pandas, numpy and dict values become Python scalars or lists). It raises `ExecError` when the result is empty or NaN. It maps a bare column letter to its column name, unless that letter is a real cell value. If you change the variables exposed to the code, update both `env` and `SYSTEM_PROMPT`.
4. **`formatter.py`** (no LLM). `parse_spec` reads the query's answer instruction:
   - Yes/No or True/False.
   - Decimals, from `two decimal places`, `nearest tenth` or `2 dp`, rounded half-up after `round(x, 9)` to remove float noise.
   - Integer, `%` sign, thousands separators and date format.
   - The list separator and the expected value `count`, taken from the instruction tail (`_instruction`) or a backtick template such as `` `men, women` ``.

   `type_problem` checks the result's type and count and returns feedback for the LLM.

`main.py` → `solve_one` works as follows:
- **Chains.** Each chain is LLM code → run → on an exception or a `type_problem`, send feedback and retry, up to `max_retries`. A mistyped answer is kept as a fallback.
- **Voting.** Default `--votes 1`: chain 0 (temperature 0) only; chain 1 (0.6) runs only when chain 0 yields nothing. With `--votes 3`, the first 2 chains run in parallel and a third runs only if they disagree; on a tie, chain 0 wins.
- **Direct fallback.** If no chain yields an answer, the LLM is asked for the answer directly and that text still goes through `format_answer`.
- **Output.** The CSV is always written in query order (`utf-8-sig`), even when the API key is missing. A failed question gets an empty answer.

## Notes

- `.env`, `Key.txt`, `logs/` and `submission.csv` are gitignored and must not ship. `.env.example` currently contains a real-looking key, so replace it with a placeholder before zipping.
- The existing `ket_qua.txt` and `logs/` show 0/20 only because every call returned HTTP 402 (no OpenRouter credits).
