@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo Creando entorno virtual...
if not exist ".venv\Scripts\python.exe" py -m venv .venv
echo Verificando librerias...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
echo.
echo === Prueba de conexion a las fuentes ===
".venv\Scripts\python.exe" probar_conexion.py
echo.
echo === Ejecutando ETL ===
".venv\Scripts\python.exe" main.py
echo.
echo === Ejecutando pruebas ===
".venv\Scripts\python.exe" -m pytest -q tests
echo.
echo Listo. Para abrir la interfaz ejecute interfaz.bat
pause
