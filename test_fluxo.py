"""Testes isolados: nenhum site, imagem ou historico real e aberto."""
import base64
import contextlib
import http.client
import importlib.util
import io
import json
import os
import struct
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("programa_testado", ROOT / "servidor_separador.py")
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)
PDF = b"%PDF-1.4\n% teste sintetico\n%%EOF\n"


def png(width=1, height=1, pixel=None):
    """PNG sintetico; `pixel(x, y)` devolve o tom de cinza."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes(v for x in range(width) for v in [pixel(x, y) if pixel else 255] * 3)
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def grade(x, y):
    """Grade 3x3 inventada, como nos testes do nucleo JS."""
    for row in range(3):
        for col in range(3):
            left, top = 10 + col * 95, 65 + row * 85
            if left <= x < left + 90 and top <= y < top + 80:
                return 70 + row * 20 + col * 7
    return 255


def grade_robo(x, y):
    """3x3 nas posicoes do robo (500x750); a 3a linha e ligada por uma faixa escura."""
    if 25 <= y < 105:
        return 40 if 13 <= x < 487 else 255          # cabecalho
    for row, top in enumerate((163, 325, 487)):
        if top <= y < top + 150:
            for left in (13, 175, 337):
                if left <= x < left + 150:
                    return 80 + row * 30
            if row == 2 and 13 <= x < 487:
                return 60                             # sem vao entre as celulas
    return 255


def grade_robo_1x3(x, y):
    """Como grade_robo, mas a 2a e a 3a linhas tambem ficam ligadas por faixas escuras."""
    if 325 <= y < 637 and 13 <= x < 487 and grade_robo(x, y) == 255:
        return 60
    return grade_robo(x, y)


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="teste_separador_")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        values = dict(APP_DIR=root, PDF_DIR=root / "pdf", RESP_DIR=root / "pdf/respostas",
                      STATE_PATH=root / "pdf/.fluxo.json", LEGACY_STATE_PATH=root / "pdf/.vigia_deepseek.json",
                      CONFIG_PATH=root / "pdf/extensao_deepseek/config_local.js",
                      SECRET="a" * 64, ENGINE=None, ROBOT=None)
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for key, value in values.items():
            self.patches.enter_context(patch.object(app, key, value))
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.engine = app.ENGINE = app.Engine()
        self.robot = app.ROBOT = app.Robo()

    def capture(self):
        return self.robot.publish(png())

    def job(self, name="novo.pdf", version="", status="aguardando"):
        (app.PDF_DIR / name).write_bytes(PDF)
        job = self.engine.create(name, version)
        job.update(status=status, sentAt=time.time(), tabId=13)
        self.engine.save()
        return job


class FlowTests(IsolatedTest):
    def test_response_saved_before_delivery_and_cleanup(self):
        old_pdf, old_txt = app.PDF_DIR / "anterior.pdf", app.RESP_DIR / "anterior.txt"
        old_pdf.write_bytes(PDF)
        old_txt.write_text("antiga", encoding="utf-8")
        job = self.job(version=self.capture())
        waiting = self.job("na_fila.pdf", status="fila")
        output = self.engine.result(job, "2,9")
        self.assertEqual((app.PDF_DIR / output).read_text(encoding="utf-8"), "2,9\n")
        self.assertEqual(self.robot.inbox.get_nowait(), (job["captura"], "2,9"))
        self.assertTrue(app.safe_pdf(waiting["name"]).exists())
        self.assertFalse(old_pdf.exists() or old_txt.exists())
        self.assertEqual(self.engine.result(job, "3"), output)
        self.assertTrue(self.robot.inbox.empty())

    def test_failed_state_save_keeps_old_files_and_does_not_deliver(self):
        older = app.RESP_DIR / "anterior.txt"
        older.write_text("antiga", encoding="utf-8")
        job = self.job(version=self.capture())
        with patch.object(app, "write_json", side_effect=PermissionError("simulada")):
            with self.assertRaises(PermissionError):
                self.engine.result(job, "2")
        self.assertTrue(older.exists())
        self.assertEqual(self.engine.job(job["id"])["status"], "aguardando")
        self.assertTrue(self.robot.inbox.empty())

    def test_late_response_saved_without_robot_delivery(self):
        job = self.job(version=self.capture())
        job["sentAt"] = time.time() - 901
        self.robot.repeticoes = app.Robo.MAX_REPETICOES
        self.engine.reap()
        self.assertEqual(job["status"], "erro")
        output = self.engine.result(job, "1")
        self.assertTrue((app.PDF_DIR / output).exists())
        self.assertTrue(self.robot.inbox.empty())

    def test_previous_capture_cannot_control_current_capture(self):
        job = self.job(version=self.capture())
        self.capture()
        self.engine.result(job, "2")
        self.assertEqual(job["entrega"], "captura_desatualizada")
        self.assertTrue(self.robot.inbox.empty())

    def test_invalid_answer_saved_without_clicks(self):
        job = self.job(version=self.capture())
        self.engine.result(job, "Escolha 2 e 9")
        self.assertEqual(job["entrega"], "formato_invalido")
        self.assertTrue(self.robot.inbox.empty())

    def test_chatgpt_failure_recaptures_automatically_with_limit(self):
        for attempt in range(app.Robo.MAX_REPETICOES):
            version = self.capture()
            job = self.job("falha%s.pdf" % attempt, version)
            app.executar(self.engine, "/api/vigia/error", {"id": job["id"], "message": "falha sintetica"})
            self.assertEqual(job["error"], "falha sintetica")
            self.assertEqual(self.robot.inbox.get_nowait(), (version, "p"))
        version = self.capture()
        job = self.job("falha_final.pdf", version)
        app.executar(self.engine, "/api/vigia/error", {"id": job["id"]})
        self.assertTrue(self.robot.inbox.empty(), "nao repete sem limite")
        self.assertTrue(self.robot.status()["aguardando"], "fica esperando o usuario")

    def test_receive_pdf_once_per_capture(self):
        version = self.capture()
        first = self.engine.receber_pdf(PDF, version)
        self.assertIs(self.engine.receber_pdf(PDF, version), first)
        self.assertEqual(len(list(app.PDF_DIR.glob("*.pdf"))), 1)
        self.capture()
        with self.assertRaises(ValueError):
            self.engine.receber_pdf(PDF, "outra-1")
        with self.assertRaises(ValueError):
            self.engine.receber_pdf(b"nao e pdf", "")

    def test_long_poll_wakes_when_pdf_arrives(self):
        threading.Timer(0.2, lambda: self.engine.receber_pdf(PDF, self.capture())).start()
        start = time.monotonic()
        value = self.engine.proximo(5)
        self.assertIsNotNone(value["job"])
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(value["extensao_versao"], app.EXT_VERSION)

    def test_preserves_state_and_drops_stale_captures_on_restart(self):
        manual = self.job(status="preparando")
        captured = self.job("captura.pdf", version="sessao-1", status="fila")
        self.engine.state["preferencias"] = {"teste": [1, 2]}
        manual["campo_antigo"] = "preservar"
        self.engine.save()
        result = app.Engine()
        self.assertEqual(result.state["preferencias"], {"teste": [1, 2]})
        self.assertEqual(result.job(manual["id"])["campo_antigo"], "preservar")
        self.assertEqual(result.job(manual["id"])["status"], "fila")
        self.assertEqual(result.job(captured["id"])["status"], "erro", "captura antiga nao vai ao ChatGPT")
        self.assertTrue(app.safe_pdf(captured["name"]).exists())

    def test_invalid_state_not_overwritten(self):
        app.STATE_PATH.write_bytes(b"invalido")
        with self.assertRaises(ValueError):
            app.Engine()
        self.assertEqual(app.STATE_PATH.read_bytes(), b"invalido")

    def test_legacy_marker_preserved_without_requeue(self):
        marker = {"name": "anterior.pdf", "key": "registro-antigo", "status": "anterior"}
        (app.PDF_DIR / marker["name"]).write_bytes(PDF)
        current = self.job(status="erro")
        self.engine.state["jobs"].insert(0, marker)
        self.engine.save()
        restored = app.Engine()
        self.assertEqual(restored.state["jobs"][0], marker)
        self.assertIsNone(restored.active())
        self.assertEqual(restored.job(current["id"]), current)

    def test_token_is_preserved_and_invalid_token_not_replaced(self):
        first = app.token()
        self.assertEqual(app.token(), first)
        app.CONFIG_PATH.write_text("invalido", encoding="utf-8")
        with self.assertRaises(ValueError):
            app.token()
        self.assertEqual(app.CONFIG_PATH.read_text(encoding="utf-8"), "invalido")

    def test_payload_checks_pdf_key(self):
        job = self.job()
        self.assertEqual(base64.b64decode(self.engine.payload(job)["data"]), PDF)
        app.safe_pdf(job["name"]).write_bytes(PDF + b"alterado")
        with self.assertRaises(ValueError):
            self.engine.payload(job)

    def test_parse_whole_answer_before_any_click(self):
        for text in ("1,2,9", "1 2 9", "1;2;9", "1,1,2,9"):
            self.assertEqual(app.parse_numbers(text), [1, 2, 9])
        for text in ("10", "0", "1,2 comando", "1\n2", "", None, "[1,2]"):
            self.assertEqual(app.parse_numbers(text), [])
        browser = app.BrowserRobo.__new__(app.BrowserRobo)
        with patch.object(browser, "frame", side_effect=AssertionError("nao tocar navegador")):
            with self.assertRaises(ValueError):
                browser.answer("1,2 comando")

    def test_instance_lock_and_materialization_preserve_token(self):
        with app.InstanceLock():
            with self.assertRaises(RuntimeError):
                with app.InstanceLock():
                    pass
        secret = app.token()
        app.materializar(app.APP_DIR)
        self.assertEqual(app.token(), secret)
        manifest = json.loads((app.CONFIG_PATH.parent / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], app.EXT_VERSION)
        self.assertNotIn("externally_connectable", manifest)

    def test_automatic_cycles_capture_pdf_answer(self):
        captures = [threading.Event() for _ in range(3)]
        answers = []

        class FakeBrowser:
            def __init__(self, stop):
                self.count = 0

            def open(self):
                pass

            def capture(self, anterior, espera=None):
                if self.count:
                    self.test.assertEqual(anterior, b"anterior", "espera a imagem mudar apos responder")
                self.count += 1
                return png()

            def gerar_pdf(self, content):
                captures[self.count - 1].set()
                return PDF

            def answer(self, value):
                answers.append(value)
                return b"anterior"

            def close_driver(self):
                pass

        FakeBrowser.test = self
        with patch.object(app, "BrowserRobo", FakeBrowser), patch.object(app.sys, "stdin", None):
            self.robot.start()
            try:
                for index, text in enumerate(("1,2", "3,4")):
                    self.assertTrue(captures[index].wait(2))
                    for _ in range(100):
                        job = self.engine.active()
                        if job:
                            break
                        time.sleep(0.02)
                    job.update(status="aguardando", sentAt=time.time())
                    self.engine.result(job, text)
                    self.assertEqual(job["entrega"], "enviado")
                self.assertTrue(captures[2].wait(2))
                self.assertEqual(answers, ["1,2", "3,4"])
            finally:
                self.robot.stop()
                self.robot.thread.join(2)
        self.assertFalse(self.robot.status()["ativo"])


class HttpTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        server = app.LocalServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(close)
        self.port = server.server_port
        self.auth = {"X-Vigia-Token": app.SECRET, "Origin": "chrome-extension://" + "a" * 32,
                     "X-Vigia-Provider": "chatgpt_web", "X-Vigia-Extension": "a" * 32, "X-Vigia-Version": "5.0.0"}

    def request(self, path, data=None, headers=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {key: value for key, value in (headers or {}).items() if value is not None}
        if isinstance(data, dict):
            data = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method or ("POST" if data is not None else "GET"), path, data, headers)
            response = connection.getresponse()
            body = response.read()
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
            if body:
                body = json.loads(body)
                self.assertTrue({"servidor", "ok", "code", "message"}.issubset(body))
            return response.status, body
        finally:
            connection.close()

    def test_status_and_no_interface(self):
        self.assertEqual(self.request("/api/status")[0], 200)
        self.assertEqual(self.request("/api/status", method="HEAD"), (200, b""))
        for path in ("/", "/numerador_matriz.html", "/api/fluxo", "/api/print", "/imagem/imagem.png"):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request("/api/status", headers={"Host": "outro.test"})[0], 403)

    def test_extension_authentication(self):
        for headers in ({**self.auth, "X-Vigia-Provider": None}, {**self.auth, "X-Vigia-Provider": "deepseek"},
                        {}, {**self.auth, "X-Vigia-Token": "errado"}, {**self.auth, "Origin": "https://outro.test"}):
            self.assertEqual(self.request("/api/vigia/next", headers=headers)[0], 403)
        self.assertEqual(self.request("/api/vigia/result", {})[0], 403)
        self.assertEqual(self.request("/api/vigia/next", headers=self.auth)[0], 200)
        self.assertTrue(self.engine.last_contact)

    def test_complete_flow_delivers_once(self):
        version = self.capture()
        identifier = self.engine.receber_pdf(PDF, version)["id"]
        self.assertEqual(self.request("/api/vigia/next?espera=1", headers=self.auth)[1]["job"]["id"], identifier)
        for route, data in (("opened", {"tabId": 4}), ("payload", {}), ("dispatch", {}),
                            ("progress", {"message": "Anexando PDF."})):
            self.assertEqual(self.request("/api/vigia/" + route, {"id": identifier, **data}, self.auth)[0], 200)
        self.assertEqual(self.request("/api/vigia/result", {"id": identifier, "tabId": 5, "text": "2"}, self.auth)[0], 400)
        for _ in range(2):
            self.assertEqual(self.request("/api/vigia/result", {"id": identifier, "tabId": 4, "text": "2,9"}, self.auth)[0], 200)
        self.assertEqual(self.robot.inbox.get_nowait(), (version, "2,9"))
        self.assertTrue(self.robot.inbox.empty())


@unittest.skipUnless(os.environ.get("TESTE_CHROME", "1") == "1", "TESTE_CHROME=0")
class ChromePdfTest(unittest.TestCase):
    """Gera o PDF de uma grade inventada no Chrome invisivel, como o robo faz."""

    def test_pdf_generated_in_browser(self):
        try:
            from selenium import webdriver
        except ImportError:
            self.skipTest("Selenium ausente")
        options = webdriver.ChromeOptions()
        for argument in ("--headless=new", "--disable-extensions", "--no-first-run", "--disable-gpu"):
            options.add_argument(argument)
        try:
            driver = webdriver.Chrome(options=options)
        except Exception as error:
            self.skipTest("Chrome indisponivel: %s" % error)
        try:
            driver.get("about:blank")
            browser = app.BrowserRobo.__new__(app.BrowserRobo)
            browser.driver = driver
            driver.set_script_timeout(30)
            start = time.monotonic()
            pdf = browser.gerar_pdf(png(300, 360, grade))
            elapsed = time.monotonic() - start
            self.assertTrue(pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF"))
            self.assertIn(b"/Subtype /Image", pdf)
            self.assertLess(elapsed, 5)
            with self.assertRaisesRegex(ValueError, "grade"):
                browser.gerar_pdf(png(300, 360))
            # Grade 3x3 inventada em que a ultima linha nao se separa (detectada como 2x3).
            mensagens = []
            with patch.object(app, "log", mensagens.append):
                pdf = browser.gerar_pdf(png(500, 750, grade_robo))
            self.assertTrue(pdf.startswith(b"%PDF-1.4"))
            self.assertTrue(any("posicoes fixas" in texto for texto in mensagens), mensagens)
            # So a 1a linha se separa (detectada como 1x3).
            mensagens.clear()
            with patch.object(app, "log", mensagens.append):
                pdf = browser.gerar_pdf(png(500, 750, grade_robo_1x3))
            self.assertTrue(pdf.startswith(b"%PDF-1.4"))
            self.assertTrue(any("posicoes fixas" in texto for texto in mensagens), mensagens)
        finally:
            driver.quit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
