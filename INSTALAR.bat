@echo off
title Instalar Separador e Robo
cd /d "%~dp0"
set "SEPARADOR_PY=py -3"
py -3 --version >nul 2>&1
if errorlevel 1 set "SEPARADOR_PY=python"
%SEPARADOR_PY% --version >nul 2>&1
if errorlevel 1 goto sem_python
echo Conferindo o Selenium ja utilizado pelo robo...
%SEPARADOR_PY% -m pip show selenium >nul 2>&1
if not errorlevel 1 goto preparar
%SEPARADOR_PY% -m pip install selenium
if errorlevel 1 goto erro
:preparar
%SEPARADOR_PY% servidor_separador.py --preparar-extensao
if errorlevel 1 goto erro
echo.
echo Arquivos prontos. Instale a extensao seguindo GUIA_INSTALACAO.txt.
echo Depois abra abrir_separador.bat.
pause
exit /b 0
:sem_python
echo Instale Python 3.12 ou superior e habilite o comando py ou python.
echo Consulte GUIA_INSTALACAO.txt.
pause
exit /b 1
:erro
echo Falha na preparacao. Feche a versao anterior do programa e confira a mensagem acima.
pause
exit /b 1
