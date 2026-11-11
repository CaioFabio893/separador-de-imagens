"""Resolve a imagem de verificacao com o PDF numerado + DeepSeek.

Recebe o print que o robo salva (imagem.png), usa a pagina local para gerar o
PDF numerado, espera a extensao do Chrome entregar esse PDF ao DeepSeek e
devolve a resposta em numeros. Nenhuma imagem e aberta, mostrada ou lida aqui:
so o texto da resposta e usado.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVIDOR = os.path.join(APP_DIR, "servidor_separador.py")
ARQUIVO_PORTA = os.path.join(APP_DIR, "tmp", "porta.txt")
PORTA_INICIAL = 8765
ESPERA_PDF = 180
ESPERA_RESPOSTA = 150
SEM_EXTENSAO = 15
ESPERA_FILA = 90
CONFIG_EXT = os.path.join(APP_DIR, "pdf", "extensao_deepseek", "config_local.js")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LeituraIncompleta(RuntimeError):
    """A imagem salva não tem a grade completa (costuma ser print pela metade)."""


def _json(url: str, timeout: float = 5) -> dict:
    with OPENER.open(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _pronto(porta: int) -> bool:
    try:
        return _json("http://127.0.0.1:%d/api/status" % porta, 1).get("servidor") == "separador"
    except (OSError, ValueError):
        return False


def _procurar() -> str:
    portas: list[int] = []
    if os.path.exists(ARQUIVO_PORTA):
        try:
            with open(ARQUIVO_PORTA, encoding="utf-8") as stream:
                portas.append(int(stream.read().strip()))
        except (OSError, ValueError):
            pass
    portas.extend(range(PORTA_INICIAL, PORTA_INICIAL + 20))
    vistas: set[int] = set()
    for porta in portas:
        if porta in vistas:
            continue
        vistas.add(porta)
        if _pronto(porta):
            return "http://127.0.0.1:%d" % porta
    return ""


def servidor() -> str:
    """Reaproveita o servidor local ou inicia um, sem abrir janela."""
    base = _procurar()
    if base:
        return base
    try:
        subprocess.Popen(
            [sys.executable, SERVIDOR, "--port", str(PORTA_INICIAL), "--port-file", ARQUIVO_PORTA],
            cwd=APP_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as erro:
        raise RuntimeError("Nao consegui iniciar o servidor local: %s" % erro) from erro
    limite = time.time() + 25
    while time.time() < limite:
        base = _procurar()
        if base:
            return base
        time.sleep(0.5)
    raise RuntimeError("O servidor local nao respondeu. Abra abrir_separador.bat e tente de novo.")


def chrome_exe() -> str:
    """Localiza o Chrome no Windows (mesmo caminho usado pelo servidor)."""
    if sys.platform != "win32":
        return ""
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                    hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as chave:
                caminho = str(winreg.QueryValueEx(chave, "")[0])
            if os.path.isfile(caminho):
                return caminho
        except OSError:
            pass
    for variavel in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        caminho = os.path.join(os.environ.get(variavel, ""), "Google", "Chrome",
                               "Application", "chrome.exe")
        if os.path.isfile(caminho):
            return caminho
    return ""


def _abrir_chrome(base: str) -> None:
    """Abre o Chrome do perfil onde a extensao esta instalada."""
    caminho = chrome_exe()
    if not caminho:
        return
    try:
        subprocess.Popen([caminho, "--profile-directory=Default",
                          base + "/numerador_matriz.html"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _extensao(base: str) -> bool:
    try:
        return bool(_json(base + "/api/fluxo").get("extensao"))
    except (OSError, ValueError):
        return False


def _esperar_extensao(base: str, segundos: int = 75) -> bool:
    limite = time.time() + segundos
    while time.time() < limite:
        if _extensao(base):
            return True
        time.sleep(2)
    return False


def _token() -> str:
    """Le o segredo local que a extensao usa (mesmo arquivo do servidor)."""
    try:
        with open(CONFIG_EXT, encoding="utf-8") as stream:
            achado = re.search(r'VIGIA_TOKEN="([a-f0-9]{64})"', stream.read())
        return achado.group(1) if achado else ""
    except OSError:
        return ""


def _cancelar(base: str, tarefa: str, motivo: str) -> None:
    """Libera a fila quando o robo desiste (evita tarefa presa por 30 min)."""
    segredo = _token()
    if not segredo:
        return
    pedido = urllib.request.Request(
        base + "/api/vigia/error",
        data=json.dumps({"id": tarefa, "message": motivo}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Vigia-Token": segredo})
    try:
        with OPENER.open(pedido, timeout=5):
            pass
    except (OSError, ValueError):
        pass


def _navegador() -> webdriver.Chrome:
    opcoes = webdriver.ChromeOptions()
    opcoes.add_argument("--headless=new")
    opcoes.add_argument("--window-size=1280,1600")
    opcoes.add_argument("--disable-gpu")
    opcoes.add_argument("--log-level=3")
    opcoes.add_argument("--mute-audio")
    opcoes.add_argument("--no-first-run")
    opcoes.add_argument("--no-default-browser-check")
    opcoes.add_argument("--disable-search-engine-choice-screen")
    return webdriver.Chrome(options=opcoes)


def _gerar_pdf(base: str, imagem: str, esperado: int = 9) -> str:
    """Abre a pagina local, envia a imagem e devolve o id da tarefa criada."""
    driver = _navegador()
    try:
        driver.set_page_load_timeout(90)
        driver.get(base + "/numerador_matriz.html")
        campo = WebDriverWait(driver, 30).until(
            lambda d: d.find_element(By.ID, "files"))
        campo.send_keys(os.path.abspath(imagem))

        # Confere a grade antes de gerar: um print pela metade nao serve.
        def leu(d):
            itens = d.find_elements(By.CSS_SELECTOR, "#list li")
            if not itens or "Tudo pronto" not in (d.find_element(By.ID, "status").text or ""):
                return False
            return itens[0].text or False

        resumo = WebDriverWait(driver, ESPERA_PDF).until(leu)
        achado = re.search(r"(\d+) imagens numeradas", resumo)
        celulas = int(achado.group(1)) if achado else 0
        if esperado and celulas != esperado:
            raise LeituraIncompleta("%d de %d imagens" % (celulas, esperado))

        botao = WebDriverWait(driver, ESPERA_PDF).until(
            lambda d: d.find_element(By.ID, "generate") if d.find_element(By.ID, "generate").is_enabled() else False)
        botao.click()

        def terminou(d):
            node = d.find_element(By.ID, "status")
            texto = node.text or ""
            if "PDF salvo" in texto:
                return texto
            if node.get_attribute("class") == "error":
                return "ERRO:" + texto
            return False

        resultado = WebDriverWait(driver, ESPERA_PDF).until(terminou)
        if resultado.startswith("ERRO:"):
            raise RuntimeError(resultado[5:].strip() or "A pagina nao conseguiu gerar o PDF.")
        tarefa = driver.execute_script(
            "return document.getElementById('fluxo').dataset.job || '';")
        if not tarefa:
            raise RuntimeError("A pagina nao informou a tarefa do PDF.")
        return tarefa
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _esperar_resposta(base: str, tarefa: str) -> str:
    limite = time.time() + ESPERA_RESPOSTA
    faltas = 0
    na_fila = time.time()
    while time.time() < limite:
        valor = _json(base + "/api/fluxo")
        job = valor.get("job") or {}
        if job.get("id") == tarefa:
            estado = job.get("status")
            if estado == "concluido":
                texto = str((job.get("resposta") or {}).get("texto") or "")
                if texto.strip():
                    return texto
                raise RuntimeError("O DeepSeek nao devolveu texto.")
            if estado == "erro":
                raise RuntimeError(str(job.get("erro") or "Falha no fluxo do PDF."))
            if estado != "fila":
                na_fila = 0
            elif na_fila and time.time() - na_fila > ESPERA_FILA:
                raise RuntimeError(
                    "A extensao PDF -> DeepSeek nao pegou o PDF. "
                    "Confira a extensao no Chrome.")
        if valor.get("extensao"):
            faltas = 0
        else:
            faltas += 1
            if faltas >= SEM_EXTENSAO:
                raise RuntimeError(
                    "A extensao PDF -> DeepSeek nao esta conectada. "
                    "Deixe o Chrome aberto com a extensao ativa.")
        time.sleep(2)
    raise RuntimeError("O DeepSeek nao respondeu em %d segundos." % ESPERA_RESPOSTA)


def numeros(texto: str) -> list[int]:
    """Extrai os numeros de 1 a 9 da resposta, na ordem em que aparecem."""
    linhas = [linha.strip().strip(".") for linha in texto.splitlines()]
    limpas = [linha for linha in linhas if linha and re.fullmatch(r"[0-9,;\s]+", linha)]
    fonte = limpas[0] if limpas else texto
    achados: list[int] = []
    for digito in re.findall(r"[0-9]", fonte):
        valor = int(digito)
        if 1 <= valor <= 9 and valor not in achados:
            achados.append(valor)
    return achados


def resolver(imagem: str, esperado: int = 9) -> list[int]:
    """Devolve os numeros das imagens que o DeepSeek indicou."""
    if not os.path.isfile(imagem):
        raise RuntimeError("Nao encontrei o print em " + imagem)
    base = servidor()
    if not _extensao(base):
        # Logo depois de iniciar o servidor a extensao demora alguns segundos
        # para se apresentar; so abre o Chrome se ela realmente nao aparecer.
        if not _esperar_extensao(base, 40):
            print("Abrindo o Chrome com a extensao PDF -> DeepSeek...")
            _abrir_chrome(base)
            if not _esperar_extensao(base):
                raise RuntimeError(
                    "A extensao PDF -> DeepSeek nao conectou. Abra o Chrome e confira "
                    "a extensao em chrome://extensions.")
    tarefa = _gerar_pdf(base, imagem, esperado)
    try:
        return numeros(_esperar_resposta(base, tarefa))
    except Exception:
        _cancelar(base, tarefa, "O robo desistiu da resposta automatica.")
        raise


if __name__ == "__main__":
    caminho = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "imagem.png")
    print("Resposta:", resolver(caminho))
