@echo off
rem Construit l'installateur dist\Rangeo-<version>-Installation.exe (lancer build.bat avant).
cd /d "%~dp0"
if not exist dist\Rangeo\Rangeo.exe (
    echo Lancez d'abord build.bat.
    exit /b 1
)
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" (
    echo Inno Setup 6 est introuvable : winget install JRSoftware.InnoSetup
    exit /b 1
)
for /f %%v in ('.venv\Scripts\python.exe -c "import rangeo; print(rangeo.__version__)"') do set VERSION=%%v
"%ISCC%" /Q /DAppVersion=%VERSION% installer\rangeo.iss || exit /b 1
echo Termine : dist\Rangeo-%VERSION%-Installation.exe
