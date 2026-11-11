#!/usr/bin/env python3
"""Servidor local do Separador de Imagens.

Uma unica funcao: receber as imagens ja recortadas em PDF, entregar o PDF a
extensao do DeepSeek e gravar a resposta em TXT. Somente biblioteca padrao.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("separador")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(APP_DIR, "pdf")
RESP_DIR = os.path.join(PDF_DIR, "respostas")
STATE_PATH = os.path.join(PDF_DIR, ".fluxo.json")
LEGACY_STATE_PATH = os.path.join(PDF_DIR, ".vigia_deepseek.json")
CONFIG_PATH = os.path.join(PDF_DIR, "extensao_deepseek", "config_local.js")
PROFILE_DIRECTORY = "Default"
MAX_PDF = 100 * 1024 * 1024
MAX_PAYLOAD = 20 * 1024 * 1024
MAX_RESULT = 5 * 1024 * 1024
LOCK = threading.RLock()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SECRET = ""
ENGINE: "Engine | None" = None


def envelope(ok: bool, code: str, message: str, **extra: object) -> dict:
    """Resposta JSON padrao do projeto."""
    return dict(servidor="separador", ok=ok, code=code, message=message, **extra)


def token() -> str:
    """Le (ou cria) o segredo compartilhado com a extensao do Chrome."""
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as stream:
            match = re.fullmatch(r'globalThis\.VIGIA_TOKEN="([a-f0-9]{64})";\s*', stream.read())
        if not match:
            raise ValueError("Configuracao local invalida.")
        return match.group(1)
    value = secrets.token_hex(32)
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8", newline="\n") as stream:
        stream.write('globalThis.VIGIA_TOKEN="' + value + '";\n')
    return value


def pdf_key(path: str) -> str:
    """Identidade do PDF (nome, tamanho e data) para evitar reenvio."""
    info = os.stat(path)
    return json.dumps([os.path.basename(path), info.st_size, info.st_mtime_ns // 1_000_000],
                      ensure_ascii=False, separators=(",", ":"))


def write_json(path: str, value: dict) -> None:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def safe_pdf(name: str) -> str:
    """Devolve o caminho de um PDF dentro da pasta autorizada."""
    if os.path.basename(name) != name or not name.lower().endswith(".pdf"):
        raise ValueError("Nome de PDF invalido.")
    path = os.path.join(PDF_DIR, name)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(PDF_DIR):
        raise ValueError("PDF fora da pasta autorizada.")
    return path


class Engine:
    """Fila de PDFs, entrega para a extensao e gravacao da resposta."""

    def __init__(self) -> None:
        self.last_contact = 0.0
        self.error = ""
        self.extension_version = ""
        self.state = self._load()
        self.save()

    def _load(self) -> dict:
        if not os.path.exists(STATE_PATH) and os.path.exists(LEGACY_STATE_PATH):
            try:
                with open(LEGACY_STATE_PATH, "rb") as source:
                    data = source.read()
                with open(STATE_PATH, "xb") as target:
                    target.write(data)
            except OSError:
                pass
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, encoding="utf-8") as stream:
                    state = json.load(stream)
            except (OSError, ValueError) as error:
                raise ValueError("Registro existente invalido; nenhum PDF sera enviado.") from error
            if (state.get("servidor") not in ("vigia-pdf", "separador")
                    or not isinstance(state.get("jobs"), list)):
                raise ValueError("Registro existente invalido; nenhum PDF sera enviado.")
            jobs = state["jobs"]
        else:
            jobs = []
        for job in jobs:
            if job.get("status") == "enviando":
                job.update(status="erro", error="Tarefa da versao anterior; nao reenviada automaticamente.")
            # "preparando" nao enviou nada ainda: a aba pode ter sumido ao reiniciar.
            if job.get("status") == "preparando":
                job.update(status="fila")
                job.pop("tabId", None)
        return dict(servidor="separador", ok=True, code="STATE",
                    message="Registro local do fluxo.", version=2, jobs=jobs)

    def save(self) -> None:
        write_json(STATE_PATH, self.state)

    def active(self) -> dict | None:
        return next((job for job in self.state["jobs"]
                     if job.get("status") in ("fila", "preparando", "aguardando")), None)

    def job(self, identity: object) -> dict | None:
        return next((job for job in self.state["jobs"] if job.get("id") == identity), None)

    def latest(self) -> dict | None:
        active = self.active()
        if active:
            return active
        done = [job for job in self.state["jobs"] if job.get("status") in ("concluido", "erro")]
        return done[-1] if done else None

    def create(self, name: str) -> dict:
        """Cria uma tarefa para um PDF que acabou de ser salvo."""
        with LOCK:
            path = safe_pdf(name)
            if not os.path.isfile(path):
                raise ValueError("PDF recebido nao encontrado.")
            job = dict(id=secrets.token_hex(16), key=pdf_key(path), name=name,
                       status="fila", created=time.time(), batch=secrets.token_hex(8))
            self.state["jobs"].append(job)
            if len(self.state["jobs"]) > 80:
                keep = [item for item in self.state["jobs"]
                        if item.get("status") in ("fila", "preparando", "aguardando")]
                done = [item for item in self.state["jobs"] if item not in keep]
                self.state["jobs"] = done[-40:] + keep
            self.save()
            return job

    def payload(self, job: dict) -> dict:
        with LOCK:
            path = safe_pdf(job["name"])
            if not os.path.isfile(path) or pdf_key(path) != job.get("key"):
                raise ValueError("PDF removido, alterado ou fora da pasta autorizada.")
            if os.path.getsize(path) > MAX_PAYLOAD:
                raise ValueError("PDF acima de 20 MB.")
            with open(path, "rb") as stream:
                data = stream.read()
            if not data.startswith(b"%PDF-"):
                raise ValueError("PDF invalido ou ainda em alteracao.")
            job["sha256"] = hashlib.sha256(data).hexdigest()
            self.save()
            return dict(name=job["name"], data=base64.b64encode(data).decode("ascii"))

    def result(self, job: dict, text: object) -> str:
        with LOCK:
            if job.get("status") == "concluido":
                return job.get("output", "")
            # Uma resposta atrasada de uma tarefa expirada ainda e salva.
            late = job.get("status") == "erro" and job.get("sentAt")
            if ((job.get("status") != "aguardando" and not late)
                    or not isinstance(text, str) or not text.strip()
                    or len(text) > MAX_RESULT):
                raise ValueError("Resposta vazia, tarefa encerrada ou resposta acima do limite.")
            if os.path.islink(RESP_DIR) or (
                    os.path.exists(RESP_DIR)
                    and getattr(os.stat(RESP_DIR), "st_file_attributes", 0) & 0x400):
                raise ValueError("A pasta respostas nao pode ser um link.")
            os.makedirs(RESP_DIR, exist_ok=True)
            if os.path.dirname(os.path.abspath(RESP_DIR)) != os.path.abspath(PDF_DIR):
                raise ValueError("Pasta de respostas fora do local autorizado.")
            content = text.strip() + "\n"
            stem = os.path.splitext(os.path.basename(job["name"]))[0][:160]
            for number in range(10000):
                name = stem + ("" if number == 0 else "_deepseek_" + str(number)) + ".txt"
                target = os.path.join(RESP_DIR, name)
                try:
                    # Criacao exclusiva preserva qualquer TXT que ja exista.
                    with open(target, "x", encoding="utf-8", newline="\n") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    break
                except FileExistsError:
                    continue
            else:
                raise ValueError("Nao foi possivel reservar um nome para a resposta.")
            job.update(status="concluido", output="respostas/" + name, finished=time.time())
            self.save()
            self.cleanup(job, name)
            if not late:
                try:
                    from resposta_deepseek_terminal import encaminhar
                    encaminhar(content)
                except Exception:
                    logger.exception("TXT salvo; falha ao encaminhar a resposta ao terminal.")
            return job["output"]

    def cleanup(self, keep: dict, keep_txt: str) -> None:
        """Apaga PDFs e TXTs antigos quando a nova resposta chega.

        PDFs ainda na fila ficam protegidos para nao quebrar a tarefa seguinte.
        """
        with LOCK:
            protected = {keep.get("key")}
            protected.update(item.get("key") for item in self.state["jobs"]
                             if item.get("status") in ("fila", "preparando", "aguardando"))
            for name in os.listdir(PDF_DIR):
                if not name.lower().endswith(".pdf"):
                    continue
                path = os.path.join(PDF_DIR, name)
                try:
                    if name != keep["name"] and pdf_key(path) not in protected:
                        os.unlink(path)
                except OSError:
                    pass
            if os.path.isdir(RESP_DIR):
                for name in os.listdir(RESP_DIR):
                    if not name.lower().endswith(".txt") or name == keep_txt:
                        continue
                    path = os.path.join(RESP_DIR, name)
                    try:
                        if os.path.isfile(path) and not os.path.islink(path):
                            os.unlink(path)
                    except OSError:
                        pass

    def page_status(self) -> dict:
        """Estado resumido para a pagina do aplicativo."""
        with LOCK:
            job = self.latest()
            value = envelope(True, "FLUXO", "Estado do fluxo.",
                             extensao=(time.time() - self.last_contact < 90),
                             extensao_versao=self.extension_version)
            if not job:
                return value
            value["job"] = dict(id=job.get("id"), status=job.get("status"),
                                pdf=job.get("name"), erro=job.get("error", ""),
                                andamento=job.get("progress", ""))
            if job.get("status") == "concluido" and job.get("output"):
                relative = str(job["output"]).replace("/", os.sep)
                path = os.path.normpath(os.path.join(PDF_DIR, relative))
                if (path == RESP_DIR or path.startswith(RESP_DIR + os.sep)) and os.path.isfile(path):
                    try:
                        with open(path, encoding="utf-8") as stream:
                            text = stream.read(MAX_RESULT)
                    except OSError:
                        text = ""
                    value["job"]["resposta"] = dict(arquivo=os.path.basename(path), texto=text)
            return value

    def reap(self) -> None:
        """Encerra tarefas travadas para a fila nao ficar presa para sempre."""
        with LOCK:
            now = time.time()
            changed = False
            for job in self.state["jobs"]:
                if job.get("status") == "aguardando" and now - job.get("sentAt", now) > 600:
                    job.update(status="erro",
                               error="Resposta nao confirmada; nao reenviado automaticamente.")
                    changed = True
            if changed:
                self.save()


def watch(engine: Engine) -> None:
    """Fecha tarefas travadas sem ficar varrendo a pasta de PDFs."""
    while True:
        time.sleep(20)
        try:
            engine.reap()
        except Exception:
            engine.error = "Erro ao verificar a fila; os dados foram preservados."


def chrome_path() -> str:
    """Localiza o Chrome no Windows sem abrir uma janela extra de console."""
    if sys.platform != "win32":
        return ""
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                    hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as entry:
                path = str(winreg.QueryValueEx(entry, "")[0])
            if os.path.isfile(path):
                return path
        except OSError:
            pass
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        path = os.path.join(os.environ.get(variable, ""), "Google", "Chrome",
                            "Application", "chrome.exe")
        if os.path.isfile(path):
            return path
    return ""


def open_chrome(url: str) -> None:
    """Abre a pagina no Chrome do perfil onde a extensao esta instalada."""
    path = chrome_path()
    try:
        if path:
            subprocess.Popen([path, "--profile-directory=" + PROFILE_DIRECTORY, url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            import webbrowser
            webbrowser.open(url)
    except OSError:
        pass


def open_folder() -> None:
    """Abre a pasta de respostas no gerenciador de arquivos."""
    os.makedirs(RESP_DIR, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(RESP_DIR)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", RESP_DIR])
    else:
        subprocess.Popen(["xdg-open", RESP_DIR])


def existing_server(start_port: int, port_file: str) -> str:
    """Devolve a URL de um separador ja ativo, se houver."""
    ports: list[int] = []
    if port_file and os.path.exists(port_file):
        try:
            with open(port_file, encoding="utf-8") as stream:
                ports.append(int(stream.read().strip()))
        except (OSError, ValueError):
            pass
    ports.extend(range(start_port, start_port + 20))
    seen: set[int] = set()
    for port in ports:
        if port in seen:
            continue
        seen.add(port)
        try:
            with OPENER.open("http://127.0.0.1:%d/api/status" % port, timeout=0.5) as response:
                value = json.loads(response.read().decode("utf-8"))
            if value.get("servidor") == "separador" and value.get("ok"):
                return "http://127.0.0.1:%d" % port
        except (OSError, ValueError):
            continue
    return ""


class Handler(BaseHTTPRequestHandler):
    """Serve a interface, o PDF e as rotas usadas pela extensao."""

    server_version = "SeparadorImagens/2.0"

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def respond(self, status: int, body: bytes, content_type: str,
                head_only: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def json_value(self, status: int, value: dict, head_only: bool = False) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.respond(status, body, "application/json; charset=utf-8", head_only)

    def json_status(self, status: int, code: str, message: str,
                    head_only: bool = False) -> None:
        self.json_value(status, envelope(status == 200, code, message), head_only)

    def same_origin(self) -> bool:
        origin = "http://127.0.0.1:" + str(self.server.server_port)
        return self.headers.get("Origin") == origin

    def extension_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        return origin == "" or re.fullmatch(r"chrome-extension://[a-p]{32}", origin) is not None

    def authorized_extension(self) -> bool:
        host = self.headers.get("Host", "")
        supplied = self.headers.get("X-Vigia-Token", "")
        return (host == "127.0.0.1:" + str(self.server.server_port)
                and self.extension_origin()
                and hmac.compare_digest(supplied.encode("utf-8"), SECRET.encode("ascii")))

    def send_error(self, code: int, message: str | None = None,
                   explain: str | None = None) -> None:
        self.json_status(code, "HTTP_ERROR", message or "Requisicao nao permitida.")

    def serve_page(self, head_only: bool = False) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/numerador_matriz.html")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        elif path == "/api/status":
            self.json_status(200, "pronto", "Separador de Imagens pronto.", head_only)
        elif path == "/numerador_matriz.html":
            try:
                with open(os.path.join(APP_DIR, "numerador_matriz.html"), "rb") as page:
                    body = page.read()
            except OSError:
                self.json_status(500, "arquivo_ausente",
                                 "Nao encontrei a interface. Confira a pasta do Separador.",
                                 head_only)
                return
            self.respond(200, body, "text/html; charset=utf-8", head_only)
        else:
            self.json_status(404, "rota_desconhecida", "Rota nao encontrada.", head_only)

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/vigia/next":
            if not self.authorized_extension():
                self.json_status(403, "acesso_negado", "Acesso nao autorizado.")
                return
            with LOCK:
                ENGINE.last_contact = time.time()
                job = ENGINE.active()
            self.json_value(200, envelope(True, "NEXT", "Fila local.", job=job))
            return
        if path == "/api/fluxo":
            self.json_value(200, ENGINE.page_status())
            return
        self.serve_page()

    def do_HEAD(self) -> None:
        self.serve_page(head_only=True)

    def do_POST(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/pdf":
            self.handle_pdf()
        elif path == "/api/extensao":
            self.handle_extension_version()
        elif path == "/api/abrir":
            self.handle_open()
        elif path.startswith("/api/vigia/"):
            self.handle_extension(path)
        else:
            self.json_status(404, "rota_desconhecida", "Rota nao encontrada.")

    def handle_pdf(self) -> None:
        if not self.same_origin():
            self.json_status(403, "origem_invalida", "Abra o programa pelo atalho local.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if (self.headers.get_content_type() != "application/pdf"
                or not 0 < length <= MAX_PDF):
            self.json_status(400, "pdf_invalido", "O PDF deve ter ate 100 MB.")
            return
        body = self.rfile.read(length)
        if (len(body) != length or not body.startswith(b"%PDF-")
                or not body.rstrip().endswith(b"%%EOF")):
            self.json_status(400, "pdf_invalido", "Nao recebi um PDF completo. Gere novamente.")
            return
        filename = "imagens_numeradas_" + uuid.uuid4().hex[:12] + ".pdf"
        path = os.path.join(PDF_DIR, filename)
        created = False
        try:
            os.makedirs(PDF_DIR, exist_ok=True)
            with open(path, "xb") as output:
                created = True
                output.write(body)
            job = ENGINE.create(filename)
        except (OSError, ValueError):
            if created:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self.json_status(500, "falha_salvar",
                             "Nao consegui salvar o PDF. Confira espaco e permissao na pasta do programa.")
            return
        self.json_value(200, envelope(
            True, "pdf_salvo", "PDF salvo. Enviando ao DeepSeek...",
            arquivo=path, job=dict(id=job["id"], status=job["status"], pdf=job["name"])))

    def handle_extension_version(self) -> None:
        """Guarda a versao da extensao que a pagina local informou."""
        if not self.same_origin():
            self.json_status(403, "origem_invalida", "Abra o programa pelo atalho local.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Requisicao invalida.")
            data = json.loads(self.rfile.read(length))
            version = str(data.get("versao", ""))[:40]
        except (ValueError, OSError, KeyError, TypeError):
            self.json_status(400, "versao_invalida", "Versao da extensao invalida.")
            return
        with LOCK:
            ENGINE.extension_version = version
        self.json_status(200, "versao_guardada", "Versao da extensao registrada.")

    def handle_open(self) -> None:
        if not self.same_origin():
            self.json_status(403, "origem_invalida", "Abra o programa pelo atalho local.")
            return
        try:
            open_folder()
        except OSError as error:
            self.json_status(500, "falha_abrir", str(error))
            return
        self.json_status(200, "pasta_aberta", "Pasta de respostas aberta.")

    def handle_extension(self, path: str) -> None:
        if not self.authorized_extension():
            self.json_status(403, "acesso_negado", "Acesso nao autorizado.")
            return
        with LOCK:
            ENGINE.last_contact = time.time()
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 30 * 1024 * 1024:
                    raise ValueError("Requisicao invalida.")
                data = json.loads(self.rfile.read(length))
                job = ENGINE.job(data.get("id"))
                if not job:
                    raise ValueError("Tarefa nao encontrada.")
                if path == "/api/vigia/opened" and job["status"] in ("fila", "preparando"):
                    if not isinstance(data.get("tabId"), int):
                        raise ValueError("Aba invalida.")
                    job.update(status="preparando", tabId=data["tabId"])
                    ENGINE.save()
                elif path == "/api/vigia/payload" and job["status"] == "preparando":
                    payload = ENGINE.payload(job)
                    self.json_value(200, envelope(True, "PDF", "PDF preparado.", **payload))
                    return
                elif path == "/api/vigia/dispatch" and job["status"] == "preparando":
                    job.update(status="aguardando", sentAt=time.time())
                    ENGINE.save()
                elif path == "/api/vigia/result":
                    if data.get("tabId") != job.get("tabId"):
                        raise ValueError("Aba nao autorizada.")
                    output = ENGINE.result(job, data.get("text"))
                    self.json_value(200, envelope(True, "SAVED", "TXT salvo.", output=output))
                    return
                elif path == "/api/vigia/progress" and job["status"] == "aguardando":
                    job["progress"] = str(data.get("message", ""))[:300]
                    ENGINE.save()
                elif path == "/api/vigia/error":
                    if job["status"] != "concluido":
                        job.update(status="erro",
                                   error=str(data.get("message", "Erro no processamento."))[:500])
                        ENGINE.save()
                else:
                    raise ValueError("Operacao nao permitida para esta tarefa.")
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.json_status(400, "FAILED", str(error))
                return
        self.json_status(200, "ACK", "Estado atualizado.")


def bind_server(start_port: int) -> tuple[ThreadingHTTPServer | None, int | None]:
    """Tenta abrir o servidor em 20 portas a partir de start_port."""
    for port in range(start_port, start_port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), Handler), port
        except OSError:
            continue
    return None, None


def cleanup(httpd: ThreadingHTTPServer, port_file: str) -> None:
    """Fecha o servidor e apaga o arquivo de porta."""
    httpd.server_close()
    if port_file:
        try:
            os.unlink(port_file)
        except OSError:
            pass


def main() -> int:
    """Ponto de entrada: abre o servidor e atende ate ser interrompido."""
    parser = argparse.ArgumentParser(description="Servidor local do Separador de Imagens")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--port-file", default="")
    parser.add_argument("--abrir", action="store_true",
                        help="abre a pagina no Chrome do perfil da extensao")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

    if args.abrir:
        existing = existing_server(args.port, args.port_file)
        if existing:
            open_chrome(existing + "/numerador_matriz.html")
            return 0

    global SECRET, ENGINE
    SECRET = token()
    ENGINE = Engine()
    threading.Thread(target=watch, args=(ENGINE,), daemon=True).start()

    httpd, chosen = bind_server(args.port)
    if httpd is None:
        logger.error("Nao encontrei uma porta livre entre %d e %d.",
                     args.port, args.port + 19)
        return 1

    httpd.daemon_threads = True
    if args.port_file:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.port_file)), exist_ok=True)
            with open(args.port_file, "w", encoding="utf-8") as stream:
                stream.write(str(chosen))
        except OSError as error:
            logger.error("Nao consegui gravar o arquivo de porta: %s", error)

    url = "http://127.0.0.1:%d/numerador_matriz.html" % chosen
    print("Separador de Imagens: " + url, flush=True)
    if args.abrir:
        open_chrome(url)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup(httpd, args.port_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
