@echo off
cd /d "%~dp0"
rem Tim du lieu mau o data\ , ..\ hoac ..\..\ ; dung "py" neu "python" khong co
set DATA=data
if not exist "%DATA%\queries.json" set DATA=..
if not exist "%DATA%\queries.json" set DATA=..\..
set PY=python
where python >nul 2>nul || set PY=py
%PY% -m pip install -q -r requirements.txt
%PY% main.py --xlsx_dir "%DATA%\xlsx_files" --queries "%DATA%\queries.json" --output submission.csv --log_dir logs
%PY% evaluate.py --pred submission.csv --gold "%DATA%\sample_submission.csv" --queries "%DATA%\queries.json" > ket_qua.txt
type ket_qua.txt
pause
