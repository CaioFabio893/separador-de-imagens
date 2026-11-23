"""Testes isolados: nenhum site, imagem ou historico real e aberto."""
import base64
import contextlib
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
from unittest.mock import Mock, patch

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


def grade_tela(x, y):
    """Desafio sintetico nas proporcoes do robo, com cabecalho e 3 linhas separadas."""
    if 25 <= y < 105 and 13 <= x < 487:
        return 40
    for row, top in enumerate((163, 325, 487)):
        if top <= y < top + 150:
            for left in (13, 175, 337):
                if left <= x < left + 150:
                    return 80 + row * 30
    return 255


def tela_com_grade(x, y):
    """Tela sintetica 1000x900 com o desafio colado em (150, 50)."""
    if 150 <= x < 650 and 50 <= y < 800:
        return grade_tela(x - 150, y - 50)
    return 255


def png_rgb(width, height, pixel):
    """PNG sintetico colorido; `pixel(x, y)` devolve (r, g, b)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\0" + bytes(v for x in range(width) for v in pixel(x, y)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def tela_com_botao(x, y):
    """Desafio em (150, 50) com botao azul (texto branco) fora da posicao da proporcao
    e um icone cinza a esquerda, que nao e o botao."""
    if 450 <= x < 620 and 705 <= y < 745:
        return (255, 255, 255) if 480 <= x < 590 and 720 <= y < 730 and x % 4 else (0, 116, 191)
    if 180 <= x < 215 and 705 <= y < 740:
        return (120, 120, 120)
    return (tela_com_grade(x, y),) * 3


def pagina_branca_larga(x, y, grade=True):
    """Pagina branca de 2000 px com barra, texto e botao na mesma altura da grade
    (tudo vira uma area branca so) e uma grade 4x4 de iscas que nao deve disparar."""
    if y < 60:
        return 70
    if grade and 700 <= x < 1200 and 50 <= y < 800:
        return grade_tela(x - 700, y - 50)
    if 1450 <= x < 1810 and 300 <= y < 660 and (x - 1450) % 90 < 80 and (y - 300) % 90 < 80:
        return 100
    if 100 <= x < 600 and 120 <= y < 880 and (y // 18) % 2 == 0 and (x // 9) % 3:
        return 60
    return 255


def tela_variada(fundo, largura=1000, ox=150, extra=None):
    """Tela sintetica com o desafio em (ox, 50) e outro conteudo ao redor."""
    def pixel(x, y):
        valor = extra(x, y) if extra else None
        if valor is not None:
            return valor
        if ox <= x < ox + 500 and 50 <= y < 800:
            return grade_tela(x - ox, y - 50)
        return fundo
    return png(largura, 900, pixel)


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="teste_separador_")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        values = dict(APP_DIR=root, PDF_DIR=root / "pdf", RESP_DIR=root / "pdf/respostas",
                      STATE_PATH=root / "pdf/.fluxo.json", LOCK_PATH=root / "pdf/.programa.lock",
                      ENGINE=None, ROBOT=None)
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for key, value in values.items():
            self.patches.enter_context(patch.object(app, key, value))
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
        # Testes isolados nunca abrem um Chrome de verdade (so os de ChromePdfTest).
        self.patches.enter_context(patch.object(app.TelaRobo, "_abrir_chrome",
                                                side_effect=AssertionError("teste nao abre Chrome real")))
        self.engine = app.ENGINE = app.Engine()
        self.robot = app.ROBOT = app.Robo()

    def capture(self):
        return self.robot.publish(png())

    def job(self, name="novo.pdf", version="", status="aguardando"):
        (app.PDF_DIR / name).write_bytes(PDF)
        job = self.engine.create(name, version)
        job.update(status=status, sentAt=time.time())
        self.engine.save()
        return job

    def find(self, engine, identifier):
        return next(job for job in engine.state["jobs"] if job.get("id") == identifier)


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
        self.assertEqual(self.find(self.engine, job["id"])["status"], "aguardando")
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
        # Nada e clicado; em producao conta como falha da API e tira outra captura sozinho.
        self.assertEqual(self.robot.inbox.get_nowait(), (job["captura"], "p"))
        self.assertTrue(self.robot.inbox.empty())

    def test_api_failure_recaptures_automatically_with_limit(self):
        for attempt in range(app.Robo.MAX_REPETICOES):
            version = self.capture()
            job = self.job("falha%s.pdf" % attempt, version)
            self.engine.falha(job, "falha sintetica")
            self.assertEqual(job["error"], "falha sintetica")
            self.assertEqual(self.robot.inbox.get_nowait(), (version, "p"))
        version = self.capture()
        job = self.job("falha_final.pdf", version)
        self.engine.falha(job, "falha sintetica")
        self.assertTrue(self.robot.inbox.empty(), "nao repete sem limite")
        self.assertTrue(self.robot.status()["aguardando"], "fica esperando o usuario")

    def test_aviso_sem_resposta_volta_a_vigiar_sozinho(self):
        version = self.capture()
        self.robot.aviso("Grade nao reconhecida (sintetico).")
        self.assertFalse(self.robot.retomar(), "espera alguem responder antes")
        with patch.object(app.Robo, "RETOMAR_SOZINHO", 0):
            self.assertTrue(self.robot.retomar())
        self.assertFalse(self.robot.status()["aguardando"])
        self.assertFalse(self.robot.retomar(), "so retoma uma vez por aviso")
        self.assertEqual(self.robot.version, version)

    def test_robo_recomeca_depois_de_erro_inesperado(self):
        """Producao: um erro qualquer (mouse, Chrome) nao encerra o programa."""
        abertos, capturou = [], threading.Event()

        class FakeBrowser:
            def __init__(self, stop):
                abertos.append(self)

            def open(self):
                if len(abertos) == 1:
                    raise RuntimeError("falha inesperada sintetica")

            def capture(self, anterior, espera=None):
                capturou.set()
                return None

            def close_driver(self):
                pass

        with patch.object(app, "TelaRobo", FakeBrowser), patch.object(app.Robo, "ESPERA_RECOMECAR", 0.01), \
                patch.object(app.sys, "stdin", None):
            self.robot.start()
            try:
                self.assertTrue(capturou.wait(5))
            finally:
                self.robot.stop()
                self.robot.thread.join(2)
        self.assertEqual(len(abertos), 2, "reabriu o Chrome e voltou a vigiar")
        self.assertFalse(self.robot.status()["ativo"])

    def test_robo_nao_insiste_se_nem_abrir(self):
        class SemSelenium:
            def __init__(self, stop):
                raise RuntimeError("Selenium ausente. Execute INSTALAR.bat.")

        saida = io.StringIO()
        with patch.object(app, "TelaRobo", SemSelenium), patch.object(app.sys, "stdin", None), \
                contextlib.redirect_stdout(saida):
            self.robot.start()
            self.robot.thread.join(5)
        self.assertFalse(self.robot.status()["ativo"])
        self.assertIn("Selenium ausente", saida.getvalue())

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

    def test_preserves_state_and_drops_stale_jobs_on_restart(self):
        antigo = self.job(status="preparando")          # tarefa da antiga extensao
        captured = self.job("captura.pdf", version="sessao-1", status="aguardando")
        pronto = self.job("pronto.pdf", status="concluido")
        self.engine.state["preferencias"] = {"teste": [1, 2]}
        antigo["campo_antigo"] = "preservar"
        self.engine.save()
        result = app.Engine()
        self.assertEqual(result.state["preferencias"], {"teste": [1, 2]})
        self.assertEqual(self.find(result, antigo["id"])["campo_antigo"], "preservar")
        for job in (antigo, captured):
            self.assertEqual(self.find(result, job["id"])["status"], "erro", "nao reenvia apos reiniciar")
            self.assertTrue(app.safe_pdf(job["name"]).exists())
        self.assertEqual(self.find(result, pronto["id"])["status"], "concluido")

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
        self.assertEqual(self.find(restored, current["id"]), current)

    def test_parse_whole_answer_before_any_click(self):
        for text in ("1,2,9", "1 2 9", "1;2;9", "1,1,2,9"):
            self.assertEqual(app.parse_numbers(text), [1, 2, 9])
        for text in ("10", "0", "1,2 comando", "1\n2", "", None, "[1,2]"):
            self.assertEqual(app.parse_numbers(text), [])

    def test_instance_lock(self):
        with app.InstanceLock():
            with self.assertRaises(RuntimeError):
                with app.InstanceLock():
                    pass
        self.assertTrue(app.LOCK_PATH.exists())

    def test_parar_durante_captura_ou_pdf_nao_cria_tarefa(self):
        for etapa in ("capture", "gerar_pdf"):
            with self.subTest(etapa=etapa):
                self.robot.stop_event.clear()
                browser = Mock()
                browser.capture.return_value = png()
                browser.gerar_pdf.return_value = PDF

                def parar(*args):
                    self.robot.stop()
                    return png() if etapa == "capture" else PDF

                getattr(browser, etapa).side_effect = parar
                self.robot.capturar(browser, None)
                if etapa == "capture":
                    browser.gerar_pdf.assert_not_called()
                self.assertEqual(self.engine.state["jobs"], [])
                self.assertFalse(list(app.PDF_DIR.glob("*.pdf")))

    def test_main_exige_chave_e_roda_ate_o_robo_parar(self):
        saida = io.StringIO()
        with patch.object(app, "chave_openai", return_value=""), contextlib.redirect_stderr(saida):
            self.assertEqual(app.main([]), 1)
        self.assertIn("chave_openai.txt", saida.getvalue())

        class RoboFalso:
            def __init__(self):
                self.thread = threading.Thread(target=lambda: None)

            def start(self):
                self.thread.start()

            def stop(self):
                pass

        with patch.object(app, "chave_openai", return_value="sk-" + "t" * 30), \
                patch.object(app, "Robo", RoboFalso), patch.object(app.signal, "signal"):
            self.assertEqual(app.main(["--modelo", "modelo-teste"]), 0)
        self.assertEqual(app.API.modelo, "modelo-teste")

    def test_automatic_cycles_capture_pdf_api_answer(self):
        captures = [threading.Event() for _ in range(3)]
        answers = []

        class FakeBrowser:
            def __init__(self, stop):
                self.count = 0
                self.jpeg = "QUJD"

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

        class FakeApi:
            modelo = "modelo-teste"
            respostas = iter(["1,2", "3,4"])

            def responder(self, jpeg):
                self.test.assertEqual(jpeg, "QUJD")
                return next(self.respostas)

        FakeBrowser.test = FakeApi.test = self
        with patch.object(app, "TelaRobo", FakeBrowser), patch.object(app, "API", FakeApi()), \
                patch.object(app.sys, "stdin", None):
            self.robot.start()
            try:
                self.assertTrue(captures[2].wait(5))
                self.assertEqual(answers, ["1,2", "3,4"])
            finally:
                self.robot.stop()
                self.robot.thread.join(2)
        self.assertFalse(self.robot.status()["ativo"])
        self.assertEqual(len(list(app.RESP_DIR.glob("*.txt"))), 1, "so a resposta mais nova fica")


class TelaTests(IsolatedTest):
    """Codificacao e logica do modo tela, sempre com dados sinteticos."""

    def test_open_sem_navegacao(self):
        urls = []

        class Driver:
            def get(self, url):
                urls.append(url)

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.driver = Driver()
        browser.open()
        self.assertEqual(urls, ["about:blank"])

    def test_codificador_png(self):
        scan = b"\0" + bytes([10, 20, 30, 40, 50, 60])
        dados = app.codificar_png(scan, 2, 1)
        self.assertTrue(dados.startswith(b"\x89PNG\r\n\x1a\n"))
        blocos, pos = {}, 8
        while pos < len(dados):
            tamanho = struct.unpack(">I", dados[pos:pos + 4])[0]
            tipo = dados[pos + 4:pos + 8]
            conteudo = dados[pos + 8:pos + 8 + tamanho]
            crc = struct.unpack(">I", dados[pos + 8 + tamanho:pos + 12 + tamanho])[0]
            self.assertEqual(crc, zlib.crc32(tipo + conteudo))
            blocos[tipo] = conteudo
            pos += 12 + tamanho
        self.assertEqual(struct.unpack(">IIBBBBB", blocos[b"IHDR"]), (2, 1, 8, 2, 0, 0, 0))
        self.assertEqual(zlib.decompress(blocos[b"IDAT"]), scan)
        self.assertEqual(blocos[b"IEND"], b"")

    @unittest.skipUnless(os.name == "nt", "API GDI do Windows")
    def test_png_tela_gdi_simulado_libera_recursos_e_reaproveita(self):
        """GDI falso (nenhuma tela real e lida): PNG correto, recursos liberados, cache."""
        estrutura = app._gdi()[2]
        user32, gdi32 = Mock(), Mock()
        user32.GetSystemMetrics.side_effect = lambda n: {76: -2, 77: 0, 78: 2, 79: 1}[n]
        user32.GetDC.return_value = 11
        gdi32.CreateCompatibleDC.return_value = 22
        gdi32.CreateCompatibleBitmap.return_value = 33
        gdi32.SelectObject.return_value = 44
        gdi32.BitBlt.return_value = 1

        def pixels(hdc, bitmap, inicio, linhas, buffer, info, uso):
            app.ctypes.memmove(buffer, bytes([10, 20, 30, 255, 40, 50, 60, 255]), 8)
            return 1

        gdi32.GetDIBits.side_effect = pixels
        with patch.object(app, "_gdi", return_value=(user32, gdi32, estrutura)), \
                patch.object(app, "_preparar_dpi"), patch.object(app, "_ULTIMA_TELA", (None, b"")), \
                patch.object(app, "scanlines_de_bgra", wraps=app.scanlines_de_bgra) as conversao:
            primeira, ox, oy = app.png_tela()
            segunda = app.png_tela()[0]
        self.assertEqual((ox, oy), (-2, 0))
        self.assertIs(segunda, primeira)
        conversao.assert_called_once()
        idat = primeira[primeira.index(b"IDAT") + 4:primeira.index(b"IEND") - 8]
        self.assertEqual(zlib.decompress(idat), b"\0" + bytes([30, 20, 10, 60, 50, 40]))
        self.assertEqual(gdi32.DeleteObject.call_args_list[-1].args, (33,))
        self.assertEqual(gdi32.DeleteDC.call_args_list[-1].args, (22,))
        self.assertEqual(user32.ReleaseDC.call_args_list[-1].args, (0, 11))
        self.assertEqual(gdi32.SelectObject.call_args_list[-1].args, (22, 44))

    def test_bgra_para_scanlines(self):
        bruto = bytes([10, 20, 30, 255, 40, 50, 60, 255])  # B,G,R,A de 2 pixels
        self.assertEqual(app.scanlines_de_bgra(bruto, 2, 1),
                         b"\0" + bytes([30, 20, 10, 60, 50, 40]))

    def test_capture_soma_offset_da_tela_virtual(self):
        recorte, tela = png(3, 2), png(2, 2)

        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        class Driver:
            def execute_async_script(self, js, fonte):
                return {"ok": True, "png": base64.b64encode(recorte).decode("ascii"),
                        "cells": {"1": [238, 288], "2": [400, 288]}, "button": [586, 765]}

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        with patch.object(app, "png_tela", return_value=(tela, -1920, 10)) as captura:
            self.assertEqual(browser.capture(), recorte)
        self.assertEqual(captura.call_count, 2, "segunda print confirma o recorte estavel")
        self.assertEqual(browser.centros, {1: (-1682, 298), 2: (-1520, 298)})
        self.assertEqual(browser.botao, (-1334, 775))
        self.assertEqual(browser.last, recorte)

    def test_capture_nao_repite_mesmo_captcha(self):
        recorte = png(3, 2)

        class Stop:
            def __init__(self):
                self.esperas = 0

            def wait(self, segundos=0):
                self.esperas += 1
                return self.esperas >= 2

            def is_set(self):
                return self.esperas >= 2

        class Driver:
            def execute_async_script(self, js, fonte):
                return {"ok": True, "png": base64.b64encode(recorte).decode("ascii"),
                        "cells": {}, "button": [0, 0]}

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        browser.centros, browser.botao = {1: (1, 1)}, (2, 2)
        with patch.object(app, "png_tela", return_value=(png(2, 2), 0, 0)), \
                patch.object(app, "INTERVALO_TELA", 0):
            self.assertIsNone(browser.capture(recorte))
        self.assertEqual(browser.centros, {})
        self.assertIsNone(browser.botao)

    def test_capture_retry_falha_js(self):
        recorte, tela = png(3, 2), png(2, 2)

        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        class Driver:
            def __init__(self):
                self.chamadas = 0

            def execute_async_script(self, js, fonte):
                self.chamadas += 1
                if self.chamadas == 1:
                    raise RuntimeError("chrome ocupado")
                return {"ok": True, "png": base64.b64encode(recorte).decode("ascii"),
                        "cells": {"1": [1, 2]}, "button": [3, 4]}

        browser = app.TelaRobo.__new__(app.TelaRobo)
        driver = Driver()
        browser.stop_event, browser.driver = Stop(), driver
        with patch.object(app, "png_tela", return_value=(tela, 0, 0)), \
                patch.object(app, "INTERVALO_TELA", 0):
            self.assertEqual(browser.capture(), recorte)
        self.assertEqual(driver.chamadas, 2)

    def test_capture_so_entrega_recorte_estavel(self):
        """Desafio trocando (ou carregando) nao e entregue ate repetir em duas prints."""
        a, b = png(3, 2), png(4, 2)
        esperas = []

        class Stop:
            def wait(self, segundos=0):
                esperas.append(segundos)
                return False

            def is_set(self):
                return False

        respostas = iter([(a, [1, 1]), (b, [2, 2]), (b, [2, 2])])

        class Driver:
            def execute_async_script(self, js, fonte):
                recorte, centro = next(respostas)
                return {"ok": True, "png": base64.b64encode(recorte).decode("ascii"),
                        "cells": {"1": centro}, "button": [0, 0]}

        telas = iter([png(1, 1), png(2, 1), png(3, 1)])
        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        with patch.object(app, "png_tela", side_effect=lambda: (next(telas), 0, 0)):
            self.assertEqual(browser.capture(b"respondido"), b)
        self.assertEqual(browser.centros, {1: (2, 2)})
        self.assertEqual(esperas[0], app.TelaRobo.ESPERA_APOS_RESPOSTA)
        self.assertEqual(esperas[1:], [app.TelaRobo.CONFIRMAR_TELA] * 2)

    def test_capture_diz_por_que_a_grade_nao_foi_aceita(self):
        class Stop:
            esperas = 0

            def wait(self, segundos=0):
                Stop.esperas += 1
                return False

            def is_set(self):
                return Stop.esperas >= 1

        class Driver:
            def execute_async_script(self, js, fonte):
                return {"ok": False, "message": "grade 3x4 na tela; areas brancas 500x750:3x4"}

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        saida = io.StringIO()
        with patch.object(app, "png_tela", return_value=(png(2, 2), 0, 0)), contextlib.redirect_stdout(saida):
            self.assertIsNone(browser.capture())
        self.assertIn("Vigiando a tela: grade 3x4 na tela; areas brancas 500x750:3x4", saida.getvalue())

    def test_aviso_da_tela_nao_repete_nem_inunda(self):
        browser = app.TelaRobo.__new__(app.TelaRobo)
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida), patch.object(app.time, "monotonic", side_effect=[0, 5, 5, 25]):
            browser.avisar("grade 3x4")   # primeiro aviso
            browser.avisar("grade 3x4")   # igual: nao repete
            browser.avisar("grade 2x2")   # diferente, mas antes de 20s
            browser.avisar("grade 2x2")   # 25s depois: avisa
        self.assertEqual(saida.getvalue().count("Vigiando a tela"), 2)

    def test_capture_responde_de_novo_se_o_mesmo_desafio_ficar_parado(self):
        """Clique que nao pegou: o mesmo desafio fica na tela e nao pode travar o robo."""
        recorte = png(3, 2)

        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        class Driver:
            def execute_async_script(self, js, fonte):
                return {"ok": True, "png": base64.b64encode(recorte).decode("ascii"),
                        "cells": {"1": [1, 1]}, "button": [0, 0]}

        relogio = iter(range(0, 1000, 6))
        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        saida = io.StringIO()
        with patch.object(app, "png_tela", return_value=(png(2, 2), 0, 0)), \
                patch.object(app.time, "monotonic", side_effect=lambda: next(relogio)), \
                contextlib.redirect_stdout(saida):
            self.assertEqual(browser.capture(recorte), recorte)
        self.assertIn("O mesmo desafio continua na tela", saida.getvalue())
        self.assertIn("respondendo de novo", saida.getvalue())
        self.assertEqual(browser.centros, {1: (1, 1)})

    def test_capture_aceita_grade_parada_com_detalhe_mudando(self):
        """Mesmas 9 posicoes com algum detalhe mudando a cada print: nao espera para sempre."""
        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        class Driver:
            chamadas = 0

            def execute_async_script(self, js, fonte):
                Driver.chamadas += 1
                return {"ok": True, "png": base64.b64encode(png(Driver.chamadas, 2)).decode("ascii"),
                        "cells": {"1": [5, 5]}, "button": [0, 0]}

        telas = iter(png(1, n) for n in range(1, 50))
        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        saida = io.StringIO()
        with patch.object(app, "png_tela", side_effect=lambda: (next(telas), 0, 0)), \
                contextlib.redirect_stdout(saida):
            self.assertEqual(browser.capture(), png(app.TelaRobo.MAX_MUDANCAS + 1, 2))
        self.assertEqual(Driver.chamadas, app.TelaRobo.MAX_MUDANCAS + 1)
        self.assertIn("algum detalhe muda", saida.getvalue())

    def test_capture_reabre_o_chrome_que_caiu_e_continua(self):
        """Producao: o Chrome invisivel cai e o programa reabre sozinho, sem ninguem digitar."""
        recorte = png(3, 2)

        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        class Morto:
            chamadas = 0

            def execute_async_script(self, js, fonte):
                Morto.chamadas += 1
                raise RuntimeError("Nenhuma conexao pode ser feita (porta 49797)")

        class Vivo:
            def execute_async_script(self, js, fonte):
                return {"ok": True, "png": base64.b64encode(recorte).decode("ascii"),
                        "cells": {"1": [1, 1]}, "button": [0, 0]}

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Morto()
        reaberturas = []

        def reabrir(motivo, parou=True):
            reaberturas.append(motivo)
            browser.driver = Vivo()
            return True

        with patch.object(app, "png_tela", side_effect=lambda: (png(2, len(reaberturas) + Morto.chamadas + 1), 0, 0)), \
                patch.object(browser, "reiniciar_chrome", side_effect=reabrir):
            self.assertEqual(browser.capture(), recorte)
        self.assertEqual(Morto.chamadas, app.TelaRobo.FALHAS_PARA_REABRIR)
        self.assertEqual(len(reaberturas), 1)
        self.assertIn("49797", reaberturas[0])

    def test_capture_desiste_se_o_chrome_nao_volta(self):
        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        class Morto:
            chamadas = 0

            def execute_async_script(self, js, fonte):
                Morto.chamadas += 1
                raise RuntimeError("sessao encerrada")

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Morto()
        with patch.object(app, "png_tela", return_value=(png(2, 2), 0, 0)), \
                patch.object(browser, "reiniciar_chrome", return_value=True) as reabrir, \
                self.assertRaisesRegex(ValueError, "Feche e abra o programa"):
            browser.capture()
        self.assertEqual(reabrir.call_count, app.TelaRobo.MAX_REINICIOS)
        self.assertEqual(Morto.chamadas, app.TelaRobo.FALHAS_PARA_REABRIR * (app.TelaRobo.MAX_REINICIOS + 1))

    def test_renova_o_chrome_aberto_ha_muito_tempo_sem_desafio(self):
        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return Stop.parar

            parar = False

        class Driver:
            def execute_async_script(self, js, fonte):
                Stop.parar = True
                return {"ok": False, "message": "grade nao encontrada"}

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        browser.aberto_em = time.monotonic() - app.TelaRobo.RENOVAR_CHROME - 1
        with patch.object(app, "png_tela", return_value=(png(2, 2), 0, 0)), \
                patch.object(browser, "reiniciar_chrome", return_value=True) as reabrir:
            self.assertIsNone(browser.capture())
        reabrir.assert_called_once()
        self.assertEqual(reabrir.call_args.kwargs, {"parou": False})

    def test_gerar_pdf_reabre_o_chrome_e_tenta_de_novo(self):
        class Morto:
            def execute_async_script(self, js, fonte):
                raise RuntimeError("chrome caiu")

        class Vivo:
            def execute_async_script(self, js, fonte):
                return {"ok": True, "pdf": base64.b64encode(PDF).decode("ascii"), "jpeg": "QUJD"}

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.driver = Morto()

        def reabrir(motivo, parou=True):
            browser.driver = Vivo()
            return True

        with patch.object(browser, "reiniciar_chrome", side_effect=reabrir):
            self.assertEqual(browser.gerar_pdf(png()), PDF)
        self.assertEqual(browser.jpeg, "QUJD")
        browser.driver = Morto()
        with patch.object(browser, "reiniciar_chrome", return_value=False), \
                self.assertRaisesRegex(ValueError, "Falha ao gerar o PDF"):
            browser.gerar_pdf(png())

    def test_capture_tela_bloqueada_continua_vigiando(self):
        recorte = png(3, 2)

        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        class Driver:
            def execute_async_script(self, js, fonte):
                return {"ok": True, "png": base64.b64encode(recorte).decode("ascii"),
                        "cells": {"1": [1, 1]}, "button": [0, 0]}

        prints = iter([RuntimeError("tela bloqueada"), (png(2, 2), 0, 0), (png(2, 2), 0, 0)])

        def captura():
            item = next(prints)
            if isinstance(item, Exception):
                raise item
            return item

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver = Stop(), Driver()
        with patch.object(app, "png_tela", side_effect=captura), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(browser.capture(), recorte)

    def test_answer_valida_antes_de_clicar(self):
        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event = threading.Event()
        with patch.object(app, "clique_humano", side_effect=AssertionError("nao clicar")):
            with self.assertRaises(ValueError):
                browser.answer("1,2 comando")
            with self.assertRaises(ValueError):
                browser.answer("1,2")
            browser.centros, browser.botao = {1: (1, 1)}, (2, 2)
            with self.assertRaisesRegex(ValueError, "Faltam coordenadas"):
                browser.answer("1,2")

    @unittest.skipUnless(os.name == "nt", "API do mouse do Windows")
    def test_cancelamento_durante_movimento_impede_clique(self):
        stop = threading.Event()
        with patch.object(app.ctypes, "windll") as dll:
            mouse = dll.user32
            mouse.GetSystemMetrics.side_effect = [0, 0, 100, 100]
            mouse.SetCursorPos.side_effect = lambda x, y: (stop.set() or True)
            app.clique_humano(10, 10, stop)
            mouse.SetCursorPos.assert_called_once()
            mouse.mouse_event.assert_not_called()

    def test_answer_clica_nos_centros_e_confirma(self):
        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.last = Stop(), b"anterior"
        browser.centros, browser.botao = {1: (10, 20), 2: (30, 40)}, (50, 60)
        cliques = []
        with patch.object(app, "clique_humano", side_effect=lambda x, y, stop: cliques.append((x, y))):
            self.assertEqual(browser.answer("1,2"), b"anterior")
        self.assertEqual(cliques, [(10, 20), (30, 40), (50, 60)])

    def test_answer_procura_o_botao_de_novo_depois_de_marcar(self):
        """O botao muda de largura ao marcar as imagens: clica onde ele esta agora."""
        class Stop:
            def wait(self, segundos=0):
                return False

            def is_set(self):
                return False

        chamadas = []

        class Driver:
            resposta = {"ok": True, "button": [90, 180]}

            def execute_async_script(self, js, fonte, grade):
                chamadas.append((js, grade))
                if isinstance(Driver.resposta, Exception):
                    raise Driver.resposta
                return Driver.resposta

        browser = app.TelaRobo.__new__(app.TelaRobo)
        browser.stop_event, browser.driver, browser.last = Stop(), Driver(), b"anterior"
        browser.centros, browser.botao = {1: (10, 20)}, (50, 60)
        browser.grade = (110.0, 400.0, 520.0, 162.0)
        cliques = []
        with patch.object(app, "clique_humano", side_effect=lambda x, y, stop: cliques.append((x, y))), \
                patch.object(app, "png_tela", return_value=(png(2, 2), 10, 20)), \
                contextlib.redirect_stdout(io.StringIO()):
            browser.answer("1")
            self.assertEqual(cliques[-1], (100, 200))
            self.assertEqual(chamadas[-1], (app.BOTAO_JS, [100.0, 390.0, 500.0, 162.0]))
            for falha in ({"ok": False, "message": "botao nao encontrado"}, RuntimeError("chrome parado")):
                Driver.resposta = falha
                browser.answer("1")
                self.assertEqual(cliques[-1], (50, 60), "sem achar, usa o botao da captura")


class ApiTests(IsolatedTest):
    """API da OpenAI simulada por um servidor local; nenhuma chamada real."""

    def setUp(self):
        super().setUp()
        import http.server
        self.calls = []
        self.reply = (200, {"output": [{"type": "reasoning"}, {"type": "message", "content": [
            {"type": "output_text", "text": "2,9"}]}]})
        test = self

        class Fake(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.calls.append((self.path, self.headers.get("Authorization"), body))
                status, value = test.reply
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = http.server.HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.patches.enter_context(patch.object(app, "OPENAI_URL", "http://127.0.0.1:%s/v1/responses" % server.server_port))
        self.patches.enter_context(patch.object(app, "API", app.ClienteOpenAI("sk-" + "t" * 30, "modelo-teste")))

    def test_api_answer_saved_and_delivered(self):
        version = self.capture()
        job = self.engine.receber_pdf(PDF, version)
        self.engine.processar_api(job, "QUJD")
        self.assertEqual(job["status"], "concluido")
        self.assertEqual(self.robot.inbox.get_nowait(), (version, "2,9"))
        path, auth, body = self.calls[0]
        self.assertEqual(path, "/v1/responses")
        self.assertEqual(auth, "Bearer sk-" + "t" * 30)
        self.assertEqual(body["model"], "modelo-teste")
        image = body["input"][0]["content"][1]
        self.assertEqual(image["image_url"], "data:image/jpeg;base64,QUJD")

    def test_api_error_triggers_new_capture(self):
        self.reply = (401, {"error": {"message": "chave invalida sintetica"}})
        version = self.capture()
        job = self.engine.receber_pdf(PDF, version)
        self.engine.processar_api(job, "QUJD")
        self.assertEqual(job["status"], "erro")
        self.assertIn("401", job["error"])
        self.assertEqual(self.robot.inbox.get_nowait(), (version, "p"))

    def test_key_from_file_only_when_valid(self):
        with patch.object(app, "CHAVE_PATH", app.APP_DIR / "chave.txt"), patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(app.chave_openai(), "")
            (app.APP_DIR / "chave.txt").write_text("sk-proj-" + "x" * 40 + "\n", encoding="utf-8")
            self.assertEqual(app.chave_openai(), "sk-proj-" + "x" * 40)
            (app.APP_DIR / "chave.txt").write_text("invalida", encoding="utf-8")
            self.assertEqual(app.chave_openai(), "")


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
            browser = app.TelaRobo.__new__(app.TelaRobo)
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

    def test_localizar_grade_na_tela_sintetica(self):
        """Localiza o desafio num print inventado e gera o PDF com o recorte."""
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
            driver.set_script_timeout(30)
            tela = png(1000, 900, tela_com_grade)
            found = found_sem_botao = driver.execute_async_script(app.LOCALIZAR_JS,
                                                                  base64.b64encode(tela).decode("ascii"))
            self.assertTrue(found.get("ok"), found)
            recorte = base64.b64decode(found["png"])
            self.assertTrue(recorte.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertAlmostEqual(found["cells"]["1"][0], 238, delta=12)
            self.assertAlmostEqual(found["cells"]["1"][1], 288, delta=12)
            self.assertAlmostEqual(found["button"][0], 586, delta=12)
            self.assertAlmostEqual(found["button"][1], 765, delta=12)
            browser = app.TelaRobo.__new__(app.TelaRobo)
            browser.driver = driver
            self.assertTrue(browser.gerar_pdf(recorte).startswith(b"%PDF-1.4"))
            # Conteudo nao branco ao lado da grade: busca pelas regioes brancas da tela.
            casos = {
                "fundo escurecido": (tela_variada(110), 0),
                "barra lateral escura": (tela_variada(255, extra=lambda x, y: 30 if x < 100 else None), 0),
                "outro monitor": (tela_variada(255, 1800, 1150, lambda x, y: 40 + (x * 7 + y * 3) % 200
                                               if x < 1000 else None), 1000),
                "pagina branca com conteudo ao lado": (png(2000, 900, pagina_branca_larga), 550),
            }
            for nome, (tela, deslocamento) in casos.items():
                with self.subTest(tela=nome):
                    found = driver.execute_async_script(app.LOCALIZAR_JS, base64.b64encode(tela).decode("ascii"))
                    self.assertTrue(found.get("ok"), found)
                    self.assertAlmostEqual(found["cells"]["1"][0], 238 + deslocamento, delta=12)
                    self.assertAlmostEqual(found["cells"]["9"][1], 612, delta=12)
                    self.assertAlmostEqual(found["button"][0], 586 + deslocamento, delta=12)
                    self.assertTrue(browser.gerar_pdf(base64.b64decode(found["png"])).startswith(b"%PDF-1.4"))
            # Duas grades iguais: usa a primeira (de cima para baixo, da esquerda para a direita).
            duas = tela_variada(255, 1300, extra=lambda x, y: grade_tela(x - 700, y - 50)
                                if 700 <= x < 1200 and 50 <= y < 800 else None)
            found = driver.execute_async_script(app.LOCALIZAR_JS, base64.b64encode(duas).decode("ascii"))
            self.assertTrue(found.get("ok"), found)
            self.assertAlmostEqual(found["cells"]["1"][0], 238, delta=12)
            # Sem botao visivel: posicao pela proporcao. Com botao azul: centro do proprio botao.
            self.assertFalse(found_sem_botao.get("botaoAchado"))
            com_botao = png_rgb(1000, 900, tela_com_botao)
            found = driver.execute_async_script(app.LOCALIZAR_JS, base64.b64encode(com_botao).decode("ascii"))
            self.assertTrue(found.get("ok"), found)
            self.assertTrue(found["botaoAchado"])
            self.assertEqual(found["button"], [535, 725])
            self.assertAlmostEqual(found["cells"]["1"][0], 238, delta=12)
            # Depois de marcar as imagens, procura o botao de novo so pela area da grade.
            de_novo = driver.execute_async_script(app.BOTAO_JS, base64.b64encode(com_botao).decode("ascii"),
                                                  found["grade"])
            self.assertEqual(de_novo, {"ok": True, "button": [535, 725]})
            sem_botao = driver.execute_async_script(app.BOTAO_JS, base64.b64encode(tela).decode("ascii"),
                                                    found["grade"])
            self.assertFalse(sem_botao["ok"])
            # Grade 4x4 de quadrados iguais nao e um desafio 3x3.
            isca = png(2000, 900, lambda x, y: pagina_branca_larga(x, y, grade=False))
            found = driver.execute_async_script(app.LOCALIZAR_JS, base64.b64encode(isca).decode("ascii"))
            self.assertFalse(found.get("ok"), found)
            self.assertIn("blocos quadrados", found["message"])
        finally:
            driver.quit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
