@echo off
rem Lance Rangeo depuis le code source (version de developpement).
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m rangeo
