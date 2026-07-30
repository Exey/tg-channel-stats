@echo off
REM Build a standalone Windows binary via PyInstaller.
REM PyInstaller does not cross-compile — run this on Windows itself.
cd /d "%~dp0"
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt -r requirements-build.txt
python build.py
