@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Primero ejecute ejecutar.bat para crear el entorno y la base de datos.
  pause
  exit /b 1
)
if not exist "data\gold\mio_cali.db" (
  echo No existe la base de datos. Primero ejecute ejecutar.bat.
  pause
  exit /b 1
)
echo Abriendo la interfaz en el navegador. Para cerrarla, cierre esta ventana.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
