@echo off
rem Construit Rangeo.exe dans dist\Rangeo (lance d'abord les tests).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creation de l'environnement Python...
    py -3.12 -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements-dev.txt || goto :error
)
echo === Tests ===
.venv\Scripts\python.exe -m pytest || goto :error
echo === Icone ===
.venv\Scripts\python.exe tools\make_icon.py || goto :error
echo === Construction de l'exe ===
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean rangeo.spec || goto :error
echo.
echo Termine : dist\Rangeo\Rangeo.exe
exit /b 0
:error
echo.
echo ECHEC de la construction.
exit /b 1
