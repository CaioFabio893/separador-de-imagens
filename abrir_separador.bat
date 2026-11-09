@echo off
setlocal
rem Atalho do Separador de Imagens para Windows.
rem Usa o Tesseract instalado em Program Files e o tessdata do usuario
rem (com por+eng) sem precisar de admin.

set "TESSDATA_PREFIX=%LOCALAPPDATA%\Tesseract-OCR\tessdata"
set "PATH=%PATH%;C:\Program Files\Tesseract-OCR;%LOCALAPPDATA%\Programs\Ollama"
set "OLLAMA_HOST=127.0.0.1:11434"

cd /d "%~dp0"

rem Abre o navegador alguns segundos depois, quando o servidor ja subiu.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 3; Start-Process 'http://127.0.0.1:8765/numerador_matriz.html'"

echo Iniciando o Separador de Imagens...
echo Feche esta janela ou pressione Ctrl+C para encerrar.
py servidor_separador.py

endlocal
