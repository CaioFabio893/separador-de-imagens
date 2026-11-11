@echo off
chcp 65001 >nul
title Robo e-CAC
cd /d "%~dp0"
python robo_ecac.py
echo.
pause
