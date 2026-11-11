@echo off
title Instalar Robo e-CAC
cd /d "%~dp0"

echo ============================================================
echo  Instalacao do Robo e-CAC
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 goto sem_python

echo Python encontrado:
python --version
echo.
echo Instalando o Selenium...
python -m pip install --upgrade selenium
if errorlevel 1 goto erro_pip

echo.
echo ============================================================
echo  Tudo pronto! Agora e so abrir o ROBO.bat
echo  O Google Chrome precisa estar instalado.
echo ============================================================
echo.
pause
exit /b 0

:sem_python
echo [ERRO] Python nao encontrado.
echo Instale o Python em https://www.python.org/downloads/
echo e MARQUE a opcao "Add python.exe to PATH" na instalacao.
echo Depois rode este INSTALAR.bat de novo.
echo.
pause
exit /b 1

:erro_pip
echo.
echo [ERRO] Nao consegui instalar o Selenium. Verifique a internet.
pause
exit /b 1
