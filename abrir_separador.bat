@echo off
title Robo - PDF - ChatGPT Web
cd /d "%~dp0"
set "SEPARADOR_PY=py -3"
py -3 --version >nul 2>&1
if errorlevel 1 set "SEPARADOR_PY=python"
%SEPARADOR_PY% servidor_separador.py
if errorlevel 1 (
  echo.
  echo Nao foi possivel iniciar. Confira a mensagem acima e o GUIA_INSTALACAO.txt.
  pause
  exit /b 1
)
