@echo off
setlocal
rem Atalho do Separador de Imagens para Windows.
rem Sobe o servidor local e abre a pagina no Chrome do perfil da extensao.
cd /d "%~dp0"

echo Iniciando o Separador de Imagens...
echo Deixe esta janela aberta. Feche-a para encerrar o servidor.
py servidor_separador.py --port 8765 --port-file "tmp\porta.txt" --abrir

if errorlevel 1 (
  echo.
  echo Nao foi possivel iniciar o Separador de Imagens.
  pause
)

endlocal
