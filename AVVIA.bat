@echo off
cd /d "%~dp0"
echo.
echo Sto aprendo il programma.
echo Lascia aperta questa finestra. Se la chiudi, il programma si ferma.
echo.

where py >nul 2>&1
if %errorlevel%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo Python e' troppo vecchio. Serve Python 3.9 o piu' recente.
    echo Aprilo da https://www.python.org/downloads/ e poi fai di nuovo doppio clic su AVVIA.
    pause
    exit /b 1
  )
  set "PYTHON=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Su questo computer manca Python.
    echo Aprilo da https://www.python.org/downloads/ , installa Python 3 e poi fai di nuovo doppio clic su AVVIA.
    pause
    exit /b 1
  )
  set "PYTHON=python"
)

if not exist "%~dp0chiave_gpt.txt" (
  echo Manca il file chiave_gpt.txt in questa cartella.
  echo I testi gia' esaminati si possono rivedere. Per esaminarne uno nuovo serve quel file.
  echo.
)

start "" "http://127.0.0.1:8765"
cd /d "%~dp0strumento"
%PYTHON% server.py
echo.
echo Il programma si e' fermato.
pause
