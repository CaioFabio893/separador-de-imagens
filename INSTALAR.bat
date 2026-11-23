@echo off
title Instalar Separador de Imagens
cd /d "%~dp0"
set "SEPARADOR_PY=py -3"
py -3 --version >nul 2>&1
if errorlevel 1 set "SEPARADOR_PY=python"
%SEPARADOR_PY% --version >nul 2>&1
if errorlevel 1 goto sem_python
echo Conferindo o Selenium usado pelo Chrome invisivel...
%SEPARADOR_PY% -m pip show selenium >nul 2>&1
if not errorlevel 1 goto pronto
%SEPARADOR_PY% -m pip install selenium
if errorlevel 1 goto erro
:pronto
echo.
if not exist chave_openai.txt echo Falta o chave_openai.txt com a chave da OpenAI (sk-...). Veja GUIA_INSTALACAO.txt.
echo Depois abra abrir_separador.bat.
pause
exit /b 0
:sem_python
echo Instale Python 3.12 ou superior e habilite o comando py ou python.
echo Consulte GUIA_INSTALACAO.txt.
pause
exit /b 1
:erro
echo Falha na instalacao do Selenium. Confira a mensagem acima.
pause
exit /b 1
