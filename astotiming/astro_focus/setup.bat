@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 goto error
python -m research.bootstrap --download
if errorlevel 1 goto error
python -m streamlit run dashboard.py
exit /b
:error
echo Setup failed. Check the error above.
pause
exit /b 1
