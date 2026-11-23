#!/usr/bin/env python3
"""Programa unico, todo no terminal: print da tela -> PDF numerado -> API da OpenAI -> TXT -> cliques.

Nao abre interface nem servidor. A cada 5s tira um print da tela (so em memoria); ao achar a
grade 3x3, gera o PDF numerado num Chrome invisivel (Selenium), envia a pagina numerada a API
da OpenAI, grava a resposta em TXT e clica nos numeros com o mouse do Windows.
"""
from __future__ import annotations
import argparse
import base64
import copy
import ctypes
import hashlib
import json
import logging
import os
import queue
import random
import re
import secrets
import signal
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

# Como .exe (PyInstaller), __file__ aponta para a pasta temporaria: usa a pasta do proprio .exe.
APP_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
PDF_DIR = APP_DIR / "pdf"
RESP_DIR = PDF_DIR / "respostas"
STATE_PATH = PDF_DIR / ".fluxo.json"
LOCK_PATH = PDF_DIR / ".programa.lock"
MAX_PDF = 20 * 1024 * 1024
MAX_RESULT = 5 * 1024 * 1024
LOCK = threading.RLock()
logger = logging.getLogger("separador")
PROVIDER = "openai_api"
VERSAO = "6.0.0"
# Print da tela inteira a cada 5s, sem navegar a nenhum site.
INTERVALO_TELA = 5.0
# 0 = fica vigilante enquanto o programa estiver aberto.
TELA_TIMEOUT = 0.0
ENGINE = ROBOT = API = None
# A chave da OpenAI e obrigatoria: o programa so responde pela API.
CHAVE_PATH = APP_DIR / "chave_openai.txt"
OPENAI_URL = "https://api.openai.com/v1/responses"
MODELO_PADRAO = "gpt-4.1"
# O modelo tendia a marcar imagens a mais: o pedido deixa claro que pode ser so uma.
PROMPT_API = ("A imagem traz um desafio: o cabecalho no topo diz o que procurar e as 9 imagens abaixo "
              "estao numeradas de 1 a 9. Examine cada imagem separadamente e escolha somente as que "
              "atendem exatamente ao pedido do cabecalho. Pode ser apenas uma imagem; nao inclua imagens "
              "apenas parecidas ou duvidosas. Responda somente com os numeros escolhidos, em ordem "
              "crescente, separados por virgula, sem nenhum outro texto.")


def primeira_linha(error):
    """So a primeira linha de um erro (as do Selenium trazem pilhas enormes)."""
    return (str(error).strip().splitlines() or ["erro"])[0]


def log(message):
    print(time.strftime("[%H:%M:%S] ") + message, flush=True)


_DPI_AWARENESS = False


def _preparar_dpi():
    """Ajusta a nocao de DPI uma unica vez (importante com mais de um monitor)."""
    global _DPI_AWARENESS
    if _DPI_AWARENESS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # por monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    _DPI_AWARENESS = True


def chunk_png(kind, data):
    """Bloco PNG com CRC, calculado somente com a biblioteca padrao."""
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def scanlines_de_bgra(raw, width, height):
    """Converte o buffer BGRA do Windows em scanlines RGB com filtro 0."""
    stride = width * 4
    scan = bytearray(height * (1 + width * 3))
    for row in range(height):
        origem = row * stride
        destino = row * (1 + width * 3)
        linha = raw[origem:origem + stride]
        rgb = bytearray(width * 3)
        rgb[0::3] = linha[2::4]
        rgb[1::3] = linha[1::4]
        rgb[2::3] = linha[0::4]
        scan[destino] = 0  # filtro PNG
        scan[destino + 1:destino + 1 + width * 3] = rgb
    return bytes(scan)


def codificar_png(scan, width, height):
    """Monta um PNG RGB de 8 bits a partir das scanlines (sem PIL)."""
    return (b"\x89PNG\r\n\x1a\n"
            + chunk_png(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            # Nivel 1: prints de tela comprimem quase igual e bem mais rapido que o nivel 6.
            + chunk_png(b"IDAT", zlib.compress(scan, 1))
            + chunk_png(b"IEND", b""))


_GDI = None
# Ultima print codificada: tela sem mudanca reaproveita o PNG em vez de recodificar.
_ULTIMA_TELA = (None, b"")


def _gdi():
    """Prepara as funcoes do Windows uma unica vez (tipos de 64 bits corretos)."""
    global _GDI
    if _GDI is not None:
        return _GDI
    from ctypes import wintypes

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = wintypes.BOOL
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                wintypes.LPVOID, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]
    gdi32.GetDIBits.restype = wintypes.UINT
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    _GDI = (user32, gdi32, BITMAPINFOHEADER)
    return _GDI


def png_tela():
    """Print da tela virtual inteira; devolve (png, offset_x, offset_y)."""
    global _ULTIMA_TELA
    if os.name != "nt":
        raise RuntimeError("O modo tela so funciona no Windows.")
    _preparar_dpi()
    user32, gdi32, BITMAPINFOHEADER = _gdi()
    # 76..79: origem e tamanho da area virtual (cobre varios monitores).
    ox, oy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    if width <= 0 or height <= 0:
        raise RuntimeError("Nao consegui medir a tela; verifique os monitores.")
    hdc = hdc_mem = bitmap = antigo = None
    try:
        hdc = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc)
        bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
        if not hdc or not hdc_mem or not bitmap:
            raise RuntimeError("Falha ao preparar a captura da tela.")
        antigo = gdi32.SelectObject(hdc_mem, bitmap)
        if not gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc, ox, oy, 0x00CC0020):
            raise RuntimeError("Falha ao copiar a tela (BitBlt).")
        info = BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.biWidth, info.biHeight = width, -height  # top-down: primeira linha no topo
        info.biPlanes, info.biBitCount = 1, 32
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(hdc, bitmap, 0, height, pixels, ctypes.byref(info), 0):
            raise RuntimeError("Falha ao ler os pixels da tela (GetDIBits).")
        raw = pixels.raw
        del pixels
    finally:
        if antigo:
            gdi32.SelectObject(hdc_mem, antigo)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if hdc_mem:
            gdi32.DeleteDC(hdc_mem)
        if hdc:
            user32.ReleaseDC(0, hdc)
    # Codifica so depois de liberar os recursos do Windows, e so se a tela mudou.
    chave = (width, height, hashlib.sha256(raw).digest())
    if _ULTIMA_TELA[0] != chave:
        _ULTIMA_TELA = (chave, codificar_png(scanlines_de_bgra(raw, width, height), width, height))
    return _ULTIMA_TELA[1], ox, oy


def clique_humano(x, y, stop_event):
    """Move o cursor com pausas curtas e clica como uma pessoa (somente Windows)."""
    if os.name != "nt":
        raise RuntimeError("Os cliques simulados so funcionam no Windows.")
    from ctypes import wintypes

    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        raise ValueError("Coordenada de clique invalida: (%r, %r)." % (x, y)) from None
    user32 = ctypes.windll.user32
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p]
    user32.mouse_event.restype = None
    origem_x, origem_y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    largura, altura = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    if not (origem_x <= x < origem_x + largura and origem_y <= y < origem_y + altura):
        raise ValueError("Coordenada de clique fora da tela: (%s, %s)." % (x, y))
    ponto = wintypes.POINT()
    if stop_event.is_set():
        return
    if not user32.GetCursorPos(ctypes.byref(ponto)):
        raise RuntimeError("Falha ao consultar a posicao do cursor.")
    passos = 6
    for indice in range(1, passos + 1):
        px = round(ponto.x + (x - ponto.x) * indice / passos)
        py = round(ponto.y + (y - ponto.y) * indice / passos)
        if not user32.SetCursorPos(px, py):
            raise RuntimeError("Falha ao mover o cursor para (%s, %s)." % (px, py))
        if stop_event.wait(random.uniform(0.02, 0.12)):
            return
    if stop_event.is_set():
        return
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    try:
        stop_event.wait(random.uniform(0.04, 0.10))
    finally:
        user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP


# Deteccao da grade, recortes, numeracao e PDF (mesma logica da antiga pagina).
# Roda dentro do navegador do robo, sem janela nem pagina extra.
NUCLEO_JS = r'''
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
function runs(arr, thr, minLen) {
  const out = []; let s = -1;
  for (let i = 0; i < arr.length; i++) {
    if (arr[i] >= thr) { if (s < 0) s = i; }
    else if (s >= 0) { if (i - s >= minLen) out.push([s, i - 1]); s = -1; }
  }
  if (s >= 0 && arr.length - s >= minLen) out.push([s, arr.length - 1]);
  return out;
}
function complement(gaps, len) {
  const out = []; let cur = 0;
  for (const [a, b] of gaps) { if (a > cur) out.push([cur, a - 1]); cur = b + 1; }
  if (cur < len) out.push([cur, len - 1]);
  return out;
}
function columnBlocks(gray, width, y0, y1, threshold, minGap, minBlock) {
  const height = y1 - y0 + 1;
  const background = new Float32Array(width);
  for (let y = y0; y <= y1; y++) {
    const offset = y * width;
    for (let x = 0; x < width; x++) {
      if (gray[offset + x] >= threshold) background[x]++;
    }
  }

  // Aceita alguns pixels escuros no vão, por exemplo após compressão.
  const gaps = runs(background, height * 0.96, minGap);
  return complement(gaps, width).filter(([a, b]) => b - a + 1 >= minBlock);
}

function findGrid(gray, width, height, threshold, minGap, rowCoverage) {
  const rowWhite = new Float32Array(height);
  for (let y = 0; y < height; y++) {
    const offset = y * width;
    for (let x = 0; x < width; x++) {
      if (gray[offset + x] >= threshold) rowWhite[y]++;
    }
  }

  const minBand = Math.max(4, Math.round(height * 0.012));
  const minBlock = Math.max(4, Math.round(width * 0.02));
  const bands = complement(runs(rowWhite, width * rowCoverage, minGap), height)
    .filter(([a, b]) => b - a + 1 >= minBand)
    .map(([y0, y1]) => {
      let blocks = columnBlocks(gray, width, y0, y1, threshold, minGap, minBlock);
      if (blocks.length > 3) {
        const widest = Math.max(...blocks.map(([a, b]) => b - a + 1));
        // Desconsidera tiras estreitas na borda da captura.
        blocks = blocks.filter(([a, b], i) =>
          (i > 0 && i < blocks.length - 1) || b - a + 1 >= widest * 0.12);
      }
      return { y0, y1, blocks };
    });

  let best = null;
  for (let start = 0; start < bands.length; start++) {
    const first = bands[start];
    const cols = first.blocks;
    if (cols.length < 2 || cols.length > 10) continue;

    const rowHeight = first.y1 - first.y0 + 1;
    const widths = cols.map(([a, b]) => b - a + 1);
    const totalWidth = widths.reduce((sum, value) => sum + value, 0);
    const narrowest = Math.min(...widths);
    const tolerance = Math.max(2, narrowest * 0.15);
    const rows = [first];

    const matchesColumns = blocks => blocks.length === cols.length && blocks.every(([a, b], col) =>
        Math.abs(a - cols[col][0]) <= tolerance && Math.abs(b - cols[col][1]) <= tolerance);
    for (let i = start + 1; i < bands.length; i++) {
      const next = bands[i];
      const gap = next.y0 - rows[rows.length - 1].y1 - 1;
      const nextHeight = next.y1 - next.y0 + 1;
      if (gap > rowHeight * 0.4) break;
      if (rows.length === 2 && cols.length === 3 && nextHeight > rowHeight * 1.3) {
        // Se a última linha encostar no rodapé, confira os vãos só na altura esperada.
        const pitch = rows[1].y0 - first.y0;
        if (Math.abs(next.y0 - rows[1].y0 - pitch) <= rowHeight * 0.12) {
          const y1 = next.y0 + Math.round((rowHeight + rows[1].y1 - rows[1].y0 + 1) / 2) - 1;
          const blocks = columnBlocks(gray, width, next.y0, y1, threshold, minGap, minBlock)
            .filter(([a, b]) => b >= cols[0][0] - tolerance && a <= cols.at(-1)[1] + tolerance);
          if (matchesColumns(blocks)) rows.push({ y0: next.y0, y1, blocks });
        }
        break;
      }
      if (!matchesColumns(next.blocks) || Math.abs(nextHeight - rowHeight) > rowHeight * 0.3) break;
      rows.push(next);
    }

    // Favorece alinhamento repetido e blocos que ocupam boa parte da captura.
    const widthBalance = narrowest / Math.max(...widths);
    const averageWidth = totalWidth / cols.length;
    const heightFactor = Math.min(1, rowHeight / (averageWidth * 0.45));
    const score = (rows.length + totalWidth / width) * (0.5 + 0.5 * widthBalance) * heightFactor;
    if (!best || score > best.score) best = { rows, cols, bands, score };
  }
  return best;
}

function findBanner(data, width, grid, left, right) {
  const firstRow = grid.rows[0];
  const gridWidth = right - left + 1;
  const minHeight = Math.max(8, (firstRow.y1 - firstRow.y0 + 1) * 0.25);
  const band = grid.bands.findLast(({ y0, y1, blocks }) =>
    y1 < firstRow.y0 && y1 - y0 + 1 >= minHeight &&
    blocks.some(([a, b]) => Math.min(b, right) - Math.max(a, left) + 1 >= gridWidth * 0.72));
  if (!band) return null;

  const [x0, x1] = band.blocks.find(([a, b]) =>
    Math.min(b, right) - Math.max(a, left) + 1 >= gridWidth * 0.72);
  const { y0, y1 } = band;
  const h = y1 - y0 + 1;
  if (x1 - x0 + 1 < h * 1.6) return null;
  const colors = new Map();
  for (let y = y0 + 2; y <= y1 - 2; y += 2) {
    for (let x = x0 + 2; x <= x1 - 2; x += 2) {
      const i = (y * width + x) * 4;
      const key = (data[i] >> 4) * 256 + (data[i + 1] >> 4) * 16 + (data[i + 2] >> 4);
      const color = colors.get(key) || { count: 0, rgb: [data[i], data[i + 1], data[i + 2]] };
      color.count++;
      colors.set(key, color);
    }
  }
  const bg = [...colors.values()].reduce((best, color) => color.count > best.count ? color : best).rgb;
  const differs = (x, y) => {
    const i = (y * width + x) * 4;
    return Math.max(Math.abs(bg[0] - data[i]), Math.abs(bg[1] - data[i + 1]),
      Math.abs(bg[2] - data[i + 2])) > 38;
  };
  const foreground = new Uint16Array(x1 - x0 + 1);
  const inset = Math.max(2, Math.round(h * 0.05));
  for (let x = x0 + inset; x <= x1 - inset; x++) {
    for (let y = y0 + inset; y <= y1 - inset; y++) foreground[x - x0] += differs(x, y);
  }
  let image = null;
  for (const [a, b] of runs(foreground, h * 0.46, Math.ceil(h * 0.42))) {
    const w = b - a + 1;
    const vertical = new Uint16Array(h);
    for (let y = y0 + 1; y < y1; y++) {
      for (let x = x0 + a; x <= x0 + b; x++) vertical[y - y0] += differs(x, y);
    }
    for (const [top, bottom] of runs(vertical, w * 0.5, Math.ceil(h * 0.42))) {
      const height = bottom - top + 1;
      if (w / height < 0.75 || w / height > 1.3) continue;
      if (!image || w * height > image.area) {
        image = { x0: x0 + a, x1: x0 + b, y0: y0 + top, y1: y0 + bottom, area: w * height };
      }
    }
  }
  return { x0, x1, y0, y1, bg, image };
}

/* Reduz a imagem (máx. 1600 px) e devolve os pixels e o mapa em tons de cinza. */
function sampleImage(img, W, H) {
  const scale = Math.min(1, 1600 / Math.max(W, H));
  const aw = Math.max(8, Math.round(W * scale));
  const ah = Math.max(8, Math.round(H * scale));
  const c = document.createElement("canvas");
  c.width = aw; c.height = ah;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, aw, ah);
  ctx.drawImage(img, 0, 0, aw, ah);

  let data;
  try { data = ctx.getImageData(0, 0, aw, ah).data; }
  catch (e) { return { error: "O navegador bloqueou a leitura dos pixels desta imagem." }; }

  const gray = new Uint8Array(aw * ah);
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    gray[p] = (data[i] * 299 + data[i + 1] * 587 + data[i + 2] * 114) / 1000;
  }
  return { data, gray, aw, ah, scale };
}

/* Tenta a grade com vários parâmetros e devolve a melhor (ou null). */
function detectBestGrid(gray, aw, ah, opt, scale) {
  const gap = Math.max(1, Math.round(opt.gapMin * scale));
  // Com o ajuste padrão, também procura divisórias de um pixel.
  const thinGap = opt.gapMin === 5 ? 1 : gap;
  const attempts = [
    [opt.whiteThr, gap, 0.985],
    [opt.whiteThr, thinGap, 0.84]
  ];
  if (opt.whiteThr === 244) {
    // Imagens claras (fotos com fundo quase branco) somem com o limite padrao.
    attempts.push([230, thinGap, 0.84],
                  [250, gap, 0.985], [250, thinGap, 0.84], [252, thinGap, 0.84]);
    // Divisorias cinza claro (escala da tela) exigem um limite mais baixo.
    attempts.push([210, thinGap, 0.84], [200, gap, 0.985],
                  [200, thinGap, 0.84], [190, thinGap, 0.84], [180, thinGap, 0.84]);
  }
  let grid = null;
  for (const [threshold, minGap, coverage] of attempts) {
    const candidate = findGrid(gray, aw, ah, threshold, minGap, coverage);
    // Na dúvida, mantém o resultado com os parâmetros mais estritos.
    if (candidate && (!grid || candidate.score > grid.score + 0.05)) grid = candidate;
  }
  return grid;
}

function analyze(img, opt) {
  const W = img.naturalWidth || img.width, H = img.naturalHeight || img.height;
  if (!W || !H) return { error: "Imagem sem dimensões válidas." };

  const sample = sampleImage(img, W, H);
  if (sample.error) return sample;
  const { data, gray, aw, ah, scale } = sample;

  const grid = detectBestGrid(gray, aw, ah, opt, scale);
  if (!grid) return { error: "Não encontrei uma grade consistente nesta imagem." };

  const { rows: gridRows } = grid;
  const cols = grid.cols.map((_, col) => [0, 1].map(edge => {
    const coordinates = gridRows.map(row => row.blocks[col][edge]).sort((a, b) => a - b);
    return coordinates[Math.floor(coordinates.length / 2)];
  }));
  const toOrig = (a, b, sampleSize, originalSize) => {
    const start = clamp(Math.round(a * originalSize / sampleSize), 0, originalSize - 1);
    const end = clamp(Math.round((b + 1) * originalSize / sampleSize) - 1, start, originalSize - 1);
    return [start, end];
  };
  const box = ({ x0, x1, y0, y1 }, n) => {
    const [x, right] = toOrig(x0, x1, aw, W);
    const [y, bottom] = toOrig(y0, y1, ah, H);
    return { n, x, y, w: right - x + 1, h: bottom - y + 1 };
  };
  const header = findBanner(data, aw, grid, cols[0][0], cols.at(-1)[1]);
  const banner = header && {
    ...box(header, "banner-text"), bg: header.bg,
    image: header.image && box(header.image, "banner-image")
  };

  const cells = [];
  let n = 0;
  for (let r = 0; r < gridRows.length; r++) {
    const [y0, y1] = toOrig(gridRows[r].y0, gridRows[r].y1, ah, H);
    for (let col = 0; col < cols.length; col++) {
      const [x0, x1] = toOrig(cols[col][0], cols[col][1], aw, W);
      n++;
      cells.push({
        n, linha: r + 1, coluna: col + 1,
        x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1
      });
    }
  }

  return {
    width: W, height: H,
    rows: gridRows.length,
    cols: cols.length,
    banner,
    cells,
    scale
  };
}

function headerBox(image, result) {
  if (result.banner) return result.banner;
  const cells = result.cells;
  const x = Math.min(...cells.map(cell => cell.x));
  const right = Math.max(...cells.map(cell => cell.x + cell.w));
  const top = Math.min(...cells.map(cell => cell.y));
  if (top < 12) throw new Error("Inclua o retângulo do cabeçalho acima da grade na imagem.");
  return { x, y: 0, w: right - x, h: top };
}

function fitImage(ctx, image, crop, x, y, width, height) {
  const scale = Math.min(width / crop.w, height / crop.h);
  const w = crop.w * scale, h = crop.h * scale;
  ctx.drawImage(image, crop.x, crop.y, crop.w, crop.h,
    x + (width - w) / 2, y + (height - h) / 2, w, h);
}

function renderPdfPage(record, pageNumber, total) {
  const canvas = document.createElement("canvas");
  canvas.width = 1240; canvas.height = 1754;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#172033"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.font = "600 30px Arial, sans-serif";
  ctx.fillText("Responda apenas com o número", 620, 52);
  ctx.fillText("correspondente à imagem.", 620, 90);
  ctx.font = "italic 26px Arial, sans-serif";
  ctx.fillText("Sua resposta deve conter apenas dígitos e vírgulas.", 620, 132);
  const margin = 64, width = canvas.width - margin * 2;
  const header = record.header;
  const headerHeight = Math.min(340, Math.max(100, width * header.h / header.w));
  ctx.strokeStyle = "#9da8ba"; ctx.lineWidth = 2;
  ctx.strokeRect(margin, 160, width, headerHeight);
  fitImage(ctx, record.image, header, margin + 8, 168, width - 16, headerHeight - 16);
  const startY = 160 + headerHeight + 32;
  const gap = 18, rows = record.result.rows, cols = record.result.cols;
  const cellWidth = (width - gap * (cols - 1)) / cols;
  const availableHeight = (canvas.height - 90 - startY - gap * (rows - 1)) / rows;
  const aspect = Math.max(...record.result.cells.map(cell => cell.h / cell.w));
  const cellHeight = Math.min(availableHeight, (cellWidth - 10) * aspect + 48);
  if (cellWidth < 45 || cellHeight < 65) throw new Error("A grade é grande demais para ficar legível em uma página.");
  for (const cell of record.result.cells) {
    const x = margin + (cell.coluna - 1) * (cellWidth + gap);
    const y = startY + (cell.linha - 1) * (cellHeight + gap);
    ctx.strokeStyle = "#d5dce7"; ctx.strokeRect(x, y, cellWidth, cellHeight);
    fitImage(ctx, record.image, cell, x + 5, y + 5, cellWidth - 10, cellHeight - 48);
    ctx.fillStyle = "#172033"; ctx.font = "700 30px Arial, sans-serif";
    ctx.fillText(String(cell.n), x + cellWidth / 2, y + cellHeight - 22);
  }
  ctx.font = "20px Arial, sans-serif"; ctx.fillStyle = "#667085";
  ctx.fillText("Página " + pageNumber + " de " + total, 620, 1714);
  return canvas;
}

// PDF local: uma imagem JPEG por página, sem bibliotecas nem envio ao servidor.
function makePdf(pages) {
  if (!pages.length) throw new Error("Selecione pelo menos uma imagem.");
  const encoder = new TextEncoder();
  const chunks = [], offsets = [0];
  let length = 0;
  const push = value => {
    const bytes = typeof value === "string" ? encoder.encode(value) : value;
    chunks.push(bytes); length += bytes.length;
  };
  const object = (id, body) => {
    offsets[id] = length; push(id + " 0 obj\n"); push(body); push("\nendobj\n");
  };
  push("%PDF-1.4\n%local-pdf\n");
  object(1, "<< /Type /Catalog /Pages 2 0 R >>");
  object(2, "<< /Type /Pages /Count " + pages.length + " /Kids [" +
    pages.map((_, i) => (3 + i * 3) + " 0 R").join(" ") + "] >>");
  pages.forEach((page, index) => {
    const id = 3 + index * 3;
    object(id, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595.28 841.89] /Resources << /XObject << /Im0 " +
      (id + 1) + " 0 R >> >> /Contents " + (id + 2) + " 0 R >>");
    offsets[id + 1] = length;
    push((id + 1) + " 0 obj\n<< /Type /XObject /Subtype /Image /Width " + page.width +
      " /Height " + page.height + " /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length " +
      page.jpeg.length + " >>\nstream\n");
    push(page.jpeg); push("\nendstream\nendobj\n");
    const commands = "q\n595.28 0 0 841.89 0 0 cm\n/Im0 Do\nQ\n";
    object(id + 2, "<< /Length " + encoder.encode(commands).length + " >>\nstream\n" + commands + "endstream");
  });
  const xref = length, count = 3 + pages.length * 3;
  push("xref\n0 " + count + "\n0000000000 65535 f \n");
  for (let id = 1; id < count; id++) push(String(offsets[id]).padStart(10, "0") + " 00000 n \n");
  push("trailer\n<< /Size " + count + " /Root 1 0 R >>\nstartxref\n" + xref + "\n%%EOF\n");
  return new Blob(chunks, { type: "application/pdf" });
}
'''

GERAR_PDF_JS = r'''
const fonte = arguments[0], concluir = arguments[arguments.length - 1];
(async () => {
  "use strict";
''' + NUCLEO_JS + r'''
  // O desafio do robo e sempre 3x3, com linhas em 31,7%, 53,3% e 74,9% da altura
  // (as mesmas posicoes dos cliques). Se uma linha nao se separou do fundo, ela e
  // reconstruida com o espacamento das outras duas.
  function completarGrade(result, H) {
    // Achou as 3 colunas mas nao as 3 linhas: monta as linhas pelas posicoes fixas dos cliques.
    if (result.cols !== 3 || result.rows >= 3 || result.rows < 1) return false;
    const CENTROS = [0.317, 0.533, 0.749];
    const colunas = [1, 2, 3].map(coluna => result.cells.find(cell => cell.coluna === coluna));
    const largura = Math.min(...colunas.map(cell => cell.w));
    const altura = Math.round(Math.min(largura, H * 0.2));
    let n = 0;
    result.cells = [];
    CENTROS.forEach((centro, linha) => {
      const y = Math.max(0, Math.min(H - altura, Math.round(centro * H - altura / 2)));
      for (const cell of colunas) result.cells.push({...cell, n: ++n, linha: linha + 1, y, h: altura});
    });
    result.rows = 3;
    return true;
  }

  const bytes = Uint8Array.from(atob(fonte), c => c.charCodeAt(0));
  const image = await createImageBitmap(new Blob([bytes], {type: "image/png"}));
  try {
    const result = analyze(image, {whiteThr: 244, gapMin: 5});
    if (result.error) throw Error(result.error);
    const completada = completarGrade(result, image.height);
    if (result.rows !== 3 || result.cols !== 3) throw Error("grade " + result.rows + "x" + result.cols + " na captura de " + image.width + "x" + image.height + " px; esperado 3x3");
    const canvas = renderPdfPage({image, result, header: headerBox(image, result)}, 1, 1);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.95));
    if (!blob) throw Error("Nao consegui preparar a pagina do PDF.");
    const jpeg = new Uint8Array(await blob.arrayBuffer());
    const pdf = new Uint8Array(await makePdf([{width: canvas.width, height: canvas.height, jpeg}]).arrayBuffer());
    let binary = "";
    for (let i = 0; i < pdf.length; i += 32768) binary += String.fromCharCode.apply(null, pdf.subarray(i, i + 32768));
    let foto = "";
    for (let i = 0; i < jpeg.length; i += 32768) foto += String.fromCharCode.apply(null, jpeg.subarray(i, i + 32768));
    return {ok: true, pdf: btoa(binary), jpeg: btoa(foto), completada};
  } finally { image.close(); }
})().then(concluir, error => concluir({ok: false, message: String((error && error.message) || error)}));
'''

# Botao de confirmar (sem OCR): o maior bloco colorido e largo logo abaixo da grade, do meio
# para a direita. gx0/gx1: bordas da grade; gy1: base da ultima linha; passo: distancia entre
# centros. Ignora blocos que encostam na grade (pedacos das fotos). Devolve [x, y] ou null.
ACHAR_BOTAO_JS = r'''
function acharBotao(image, gx0, gx1, gy1, passo) {
  const limitar = (v, a, b) => Math.max(a, Math.min(b, v));
  const bx0 = limitar(Math.floor(gx0 + (gx1 - gx0) * 0.3), 0, image.width - 1);
  const bx1 = limitar(Math.ceil(gx1 + passo * 0.5), bx0 + 1, image.width);
  const by0 = limitar(Math.floor(gy1 + 2), 0, image.height - 1);
  const by1 = limitar(Math.ceil(gy1 + passo * 1.1), by0 + 1, image.height);
  const bw = bx1 - bx0, bh = by1 - by0;
  if (bh <= 4) return null;
  const area = document.createElement("canvas");
  area.width = bw; area.height = bh;
  const actx = area.getContext("2d", {willReadFrequently: true});
  actx.drawImage(image, bx0, by0, bw, bh, 0, 0, bw, bh);
  const px = actx.getImageData(0, 0, bw, bh).data;
  const cor = new Uint8Array(bw * bh);
  for (let i = 0, p = 0; p < cor.length; i += 4, p++) {
    const alto = Math.max(px[i], px[i + 1], px[i + 2]), baixo = Math.min(px[i], px[i + 1], px[i + 2]);
    cor[p] = alto - baixo >= 60 && alto >= 80 ? 1 : 0;
  }
  const pilha = new Int32Array(bw * bh);
  let melhor = null;
  for (let inicio = 0; inicio < cor.length; inicio++) {
    if (cor[inicio] !== 1) continue;
    let topo = 0, total = 0, x0 = bw, x1 = 0, y0 = bh, y1 = 0;
    cor[inicio] = 2; pilha[topo++] = inicio;
    while (topo) {
      const p = pilha[--topo], x = p % bw, y = (p - x) / bw;
      total++;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
      if (x > 0 && cor[p - 1] === 1) { cor[p - 1] = 2; pilha[topo++] = p - 1; }
      if (x < bw - 1 && cor[p + 1] === 1) { cor[p + 1] = 2; pilha[topo++] = p + 1; }
      if (y > 0 && cor[p - bw] === 1) { cor[p - bw] = 2; pilha[topo++] = p - bw; }
      if (y < bh - 1 && cor[p + bw] === 1) { cor[p + bw] = 2; pilha[topo++] = p + bw; }
    }
    const lw = x1 - x0 + 1, lh = y1 - y0 + 1;
    if (y0 === 0 || lw < passo * 0.35 || lh < passo * 0.12 || lh > passo * 0.7 || lw < lh * 1.5) continue;
    if (total < lw * lh * 0.4) continue;
    if (!melhor || lw * lh > melhor.area) {
      melhor = {area: lw * lh, x: bx0 + (x0 + x1 + 1) / 2, y: by0 + (y0 + y1 + 1) / 2};
    }
  }
  return melhor && [Math.round(melhor.x), Math.round(melhor.y)];
}
'''

# Localiza a grade no print da tela inteira e devolve so o retangulo do desafio.
# O recorte volta para GERAR_PDF_JS, que continua gerando o PDF como antes.
LOCALIZAR_JS = r'''
const fonte = arguments[0], concluir = arguments[arguments.length - 1];
(async () => {
  "use strict";
''' + NUCLEO_JS + ACHAR_BOTAO_JS + r'''
  const OPCOES = {whiteThr: 244, gapMin: 5};
  const e3x3 = result => !result.error && result.rows === 3 && result.cols === 3;

  // Regioes quase brancas e conectadas (como o painel do desafio), da maior para a menor.
  // Servem quando a tela inteira tem outro monitor, janela escura ou fundo escurecido ao lado.
  function regioesBrancas(image) {
    const escala = Math.min(1, 800 / Math.max(image.width, image.height));
    const w = Math.max(1, Math.round(image.width * escala)), h = Math.max(1, Math.round(image.height * escala));
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", {willReadFrequently: true});
    ctx.drawImage(image, 0, 0, w, h);
    const data = ctx.getImageData(0, 0, w, h).data;
    const livre = new Uint8Array(w * h);
    for (let i = 0, p = 0; p < livre.length; i += 4, p++) {
      livre[p] = data[i] * 299 + data[i + 1] * 587 + data[i + 2] * 114 >= 230000 ? 1 : 0;
    }
    const pilha = new Int32Array(w * h), minimo = 100 * escala, regioes = [];
    for (let inicio = 0; inicio < livre.length; inicio++) {
      if (livre[inicio] !== 1) continue;
      let topo = 0, x0 = w, x1 = 0, y0 = h, y1 = 0;
      livre[inicio] = 2; pilha[topo++] = inicio;
      while (topo) {
        const p = pilha[--topo], x = p % w, y = (p - x) / w;
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
        if (x > 0 && livre[p - 1] === 1) { livre[p - 1] = 2; pilha[topo++] = p - 1; }
        if (x < w - 1 && livre[p + 1] === 1) { livre[p + 1] = 2; pilha[topo++] = p + 1; }
        if (y > 0 && livre[p - w] === 1) { livre[p - w] = 2; pilha[topo++] = p - w; }
        if (y < h - 1 && livre[p + w] === 1) { livre[p + w] = 2; pilha[topo++] = p + w; }
      }
      if (x1 - x0 + 1 < minimo || y1 - y0 + 1 < minimo) continue;
      const x = clamp(Math.floor((x0 - 1) / escala), 0, image.width - 1);
      const y = clamp(Math.floor((y0 - 1) / escala), 0, image.height - 1);
      const right = clamp(Math.ceil((x1 + 2) / escala), x + 1, image.width);
      const bottom = clamp(Math.ceil((y1 + 2) / escala), y + 1, image.height);
      // A tela inteira ja foi analisada; nao repete a mesma area.
      if ((right - x) * (bottom - y) >= image.width * image.height * 0.97) continue;
      regioes.push({x, y, w: right - x, h: bottom - y});
    }
    return regioes.sort((a, b) => b.w * b.h - a.w * a.h).slice(0, 8);
  }

  // As fotos do desafio como 9 blocos nao brancos, quase quadrados, do mesmo tamanho, em 3x3 e
  // com vaos estreitos. Acha a grade mesmo com a pagina branca ao redor e outros conteudos
  // (ou outro monitor) na mesma altura. Recusa pedacos de grades maiores (3x4, 4x4...).
  function gradePorBlocos(image) {
    const escala = Math.min(1, Math.sqrt(4e6 / (image.width * image.height)));
    const w = Math.max(1, Math.round(image.width * escala)), h = Math.max(1, Math.round(image.height * escala));
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d", {willReadFrequently: true});
    ctx.drawImage(image, 0, 0, w, h);
    const data = ctx.getImageData(0, 0, w, h).data;
    // Tenta varios limites: fotos claras nao passam no limite escuro original.
    const tentativas = [];
    for (const corte of [240000, 250000, 253000]) {
    const cheio = new Uint8Array(w * h);
    for (let i = 0, p = 0; p < cheio.length; i += 4, p++) {
      cheio[p] = data[i] * 299 + data[i + 1] * 587 + data[i + 2] * 114 < corte ? 1 : 0;
    }
    const pilha = new Int32Array(w * h), blocos = [];
    const minimo = Math.max(8, 40 * escala), maximo = Math.min(w, h) * 0.45;
    for (let inicio = 0; inicio < cheio.length; inicio++) {
      if (cheio[inicio] !== 1) continue;
      let topo = 0, total = 0, x0 = w, x1 = 0, y0 = h, y1 = 0;
      cheio[inicio] = 2; pilha[topo++] = inicio;
      while (topo) {
        const p = pilha[--topo], x = p % w, y = (p - x) / w;
        total++;
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
        if (x > 0 && cheio[p - 1] === 1) { cheio[p - 1] = 2; pilha[topo++] = p - 1; }
        if (x < w - 1 && cheio[p + 1] === 1) { cheio[p + 1] = 2; pilha[topo++] = p + 1; }
        if (y > 0 && cheio[p - w] === 1) { cheio[p - w] = 2; pilha[topo++] = p - w; }
        if (y < h - 1 && cheio[p + w] === 1) { cheio[p + w] = 2; pilha[topo++] = p + w; }
      }
      const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
      if (bw < minimo || bh < minimo || bw > maximo || bh > maximo) continue;
      if (bw / bh < 0.75 || bw / bh > 1.33 || total < bw * bh * 0.6) continue;
      blocos.push({x: x0 / escala, y: y0 / escala, w: bw / escala, h: bh / escala,
        cx: (x0 + x1 + 1) / 2 / escala, cy: (y0 + y1 + 1) / 2 / escala, lado: (bw + bh) / 2 / escala});
    }
    const parecido = (a, b) => Math.abs(a.lado - b.lado) <= 0.2 * Math.max(a.lado, b.lado);
    const achar = (base, x, y, tol) => blocos.find(item => parecido(item, base) &&
      Math.abs(item.cx - x) <= tol && Math.abs(item.cy - y) <= tol);
    let melhor = null;
    for (const a of blocos) {
      for (const b of blocos) {
        const passo = b.cx - a.cx;
        if (b === a || !parecido(a, b) || Math.abs(b.cy - a.cy) > a.lado * 0.15) continue;
        if (passo < a.lado * 0.95 || passo > a.lado * 1.35) continue;
        const tol = passo * 0.15;
        const baixo = achar(a, a.cx, a.cy + passo, passo * 0.25);
        if (!baixo) continue;
        const passoY = baixo.cy - a.cy;
        const cells = [];
        for (let r = 0; r < 3; r++) {
          for (let c = 0; c < 3; c++) {
            const item = achar(a, a.cx + c * passo, a.cy + r * passoY, tol);
            if (item) cells.push({n: r * 3 + c + 1, linha: r + 1, coluna: c + 1,
              x: item.x, y: item.y, w: item.w, h: item.h});
          }
        }
        if (cells.length !== 9) continue;
        let maior = false;
        for (let k = 0; k < 3 && !maior; k++) {
          maior = [[-1, k], [3, k], [k, -1], [k, 3]].some(([c, r]) =>
            achar(a, a.cx + c * passo, a.cy + r * passoY, tol));
        }
        if (!maior && (!melhor || a.lado > melhor.lado)) melhor = {lado: a.lado, cells};
      }
    }
    tentativas.push({blocos: blocos.length,
      result: melhor && {rows: 3, cols: 3, cells: melhor.cells, lado: melhor.lado}});
    }
    return {
      blocos: Math.max(...tentativas.map(item => item.blocos)),
      result: tentativas.reduce((melhor, item) =>
        item.result && (!melhor || item.result.lado > melhor.lado) ? item.result : melhor, null)
    };
  }

  // Achou so uma fileira de 3 imagens? Reconstrói as outras duas pelas proporcoes
  // fixas do desafio (mesma ideia de completarGrade, do GERAR_PDF_JS).
  function completarFileiras(resultado, alturaTela) {
    if (!resultado || resultado.error || resultado.cols !== 3 ||
        resultado.rows < 1 || resultado.rows >= 3) return false;
    const colunas = [1, 2, 3].map(coluna => resultado.cells.find(cell => cell.coluna === coluna));
    if (colunas.some(item => !item)) return false;
    const largura = Math.min(...colunas.map(cell => cell.w));
    const altura = Math.min(...colunas.map(cell => cell.h));
    if (!(largura > 20 && altura > 20)) return false;
    const centrosX = colunas.map(cell => cell.x + cell.w / 2);
    const centroY = colunas[0].y + colunas[0].h / 2;
    const W = (centrosX[2] - centrosX[0]) / (0.824 - 0.176);
    if (!(W > 100)) return false;
    const alturas = [altura * 5, W * 1.5];
    const razoes = [0.317, 0.533, 0.749];
    let melhor = null;
    for (const H of alturas) {
      if (!(H > 100)) continue;
      for (const razao of razoes) {
        const top = centroY - razao * H;
        if (top < 0 || top + H > alturaTela) continue;
        if (!melhor || H > melhor.H) melhor = {H, top};
      }
    }
    if (!melhor) return false;
    const left = centrosX[0] - 0.176 * W;
    let n = 0;
    resultado.cells = [];
    razoes.forEach((razao, linha) => {
      for (let coluna = 0; coluna < 3; coluna++) {
        resultado.cells.push({n: ++n, linha: linha + 1, coluna: coluna + 1,
          x: Math.round(left + [0.176, 0.500, 0.824][coluna] * W - largura / 2),
          y: Math.round(melhor.top + razao * melhor.H - altura / 2),
          w: largura, h: altura});
      }
    });
    resultado.rows = 3;
    return true;
  }

  // fetch de data: URL decodifica o base64 de forma nativa (bem mais rapido em prints grandes).
  const image = await createImageBitmap(await (await fetch("data:image/png;base64," + fonte)).blob());
  try {
    let result = analyze(image, OPCOES), origem = {x: 0, y: 0};
    if (!e3x3(result)) {
      const motivo = result.error || ("grade " + result.rows + "x" + result.cols +
        " na tela de " + image.width + "x" + image.height + " px; esperado 3x3");
      const parcial = result;
      const porBlocos = gradePorBlocos(image);
      result = porBlocos.result;
      // Diagnostico so com medidas (tamanho da area e da grade), nunca com o conteudo.
      const detalhes = [];
      for (const regiao of result ? [] : regioesBrancas(image)) {
        const parte = await createImageBitmap(image, regiao.x, regiao.y, regiao.w, regiao.h);
        try {
          const tentativa = analyze(parte, OPCOES);
          if (e3x3(tentativa)) { result = tentativa; origem = regiao; break; }
          // Em tela dupla, a fileira da tela toda pode nao caber; tente na regiao.
          if (completarFileiras(tentativa, regiao.h)) { result = tentativa; origem = regiao; break; }
          detalhes.push(regiao.w + "x" + regiao.h + ":" +
            (tentativa.error ? "sem grade" : tentativa.rows + "x" + tentativa.cols));
        } finally { parte.close(); }
      }
      if (!result && completarFileiras(parcial, image.height)) result = parcial;
      if (!result) return {ok: false, message: motivo + "; blocos quadrados " + porBlocos.blocos + "; areas brancas " +
        (detalhes.length ? detalhes.join(", ") : "nenhuma")};
    }
    // As celulas 1, 3, 4 e 7 devolvem a geometria do desafio (em coordenadas da tela).
    const cell = n => {
      const item = result.cells.find(item => item.n === n ||
        (item.linha === Math.ceil(n / 3) && item.coluna === ((n - 1) % 3) + 1));
      return item && {...item, x: item.x + origem.x, y: item.y + origem.y};
    };
    const pontos = [1, 3, 4, 7].map(cell);
    if (pontos.some(item => !item)) return {ok: false, message: "celulas da grade incompletas"};
    const [c1, c3, c4, c7] = pontos.map(item => [item.x + item.w / 2, item.y + item.h / 2]);
    const W = (c3[0] - c1[0]) / (0.824 - 0.176);
    const H = ((c4[1] - c1[1]) + (c7[1] - c4[1])) / (2 * (0.533 - 0.317));
    if (!(W > 100 && H > 100)) return {ok: false, message: "tamanho do desafio invalido na tela"};
    const left = c1[0] - 0.176 * W, top = c1[1] - 0.317 * H;
    const x0 = clamp(Math.floor(left), 0, image.width - 1);
    const y0 = clamp(Math.floor(top), 0, image.height - 1);
    const x1 = clamp(Math.ceil(left + W), x0 + 1, image.width);
    const y1 = clamp(Math.ceil(top + H), y0 + 1, image.height);
    if (x1 - x0 < 100 || y1 - y0 < 100) return {ok: false, message: "recorte do desafio muito pequeno"};
    const canvas = document.createElement("canvas");
    canvas.width = x1 - x0;
    canvas.height = y1 - y0;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, x0, y0, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
    const recorte = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
    if (!recorte) return {ok: false, message: "Nao consegui recortar o desafio da tela."};
    const dados = new Uint8Array(await recorte.arrayBuffer());
    let binario = "";
    for (let i = 0; i < dados.length; i += 32768) binario += String.fromCharCode.apply(null, dados.subarray(i, i + 32768));
    const cells = {};
    for (let n = 1; n <= 9; n++) {
      const alvo = cell(n);
      if (alvo) cells[n] = [Math.round(alvo.x + alvo.w / 2), Math.round(alvo.y + alvo.h / 2)];
    }
    // Botao (Pular/Verificar/Proximo) achado na print; sem achar, proporcao fixa do desafio.
    // `grade` volta para o Python procurar o botao de novo depois de marcar as imagens.
    const passo = (c3[0] - c1[0]) / 2;
    const todas = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(cell).filter(Boolean);
    const grade = [Math.min(...todas.map(c => c.x)), Math.max(...todas.map(c => c.x + c.w)),
      Math.max(c7[1] + passo * 0.5, ...todas.map(c => c.y + c.h)), passo];
    const achado = acharBotao(image, grade[0], grade[1], grade[2], grade[3]);
    const button = achado || [Math.round(left + 0.872 * W), Math.round(top + 0.953 * H)];
    return {ok: true, png: btoa(binario), cells, button, botaoAchado: Boolean(achado), grade};
  } finally { image.close(); }
})().then(concluir, error => concluir({ok: false, message: String((error && error.message) || error)}));
'''

# Procura so o botao numa print nova (depois de marcar as imagens, o texto e a largura mudam).
BOTAO_JS = r'''
const fonte = arguments[0], grade = arguments[1], concluir = arguments[arguments.length - 1];
(async () => {
  "use strict";
''' + ACHAR_BOTAO_JS + r'''
  const image = await createImageBitmap(await (await fetch("data:image/png;base64," + fonte)).blob());
  try {
    const button = acharBotao(image, grade[0], grade[1], grade[2], grade[3]);
    return button ? {ok: true, button} : {ok: false, message: "botao nao encontrado"};
  } finally { image.close(); }
})().then(concluir, error => concluir({ok: false, message: String((error && error.message) || error)}));
'''

def safe_directory(path):
    path = Path(path)
    root = APP_DIR.resolve()
    if not path.absolute().is_relative_to(root):
        raise ValueError("Pasta fora do programa.")
    current = root
    for part in path.absolute().relative_to(root).parts:
        current = current / part
        if current.is_symlink() or (current.exists() and
                getattr(current.stat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("A pasta de dados nao pode ser um link.")
    path.mkdir(parents=True, exist_ok=True)


def atomic_bytes(path, content):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + secrets.token_hex(4))
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def safe_pdf(name):
    if (not isinstance(name, str) or Path(name).name != name or
            "/" in name or "\\" in name or not name.lower().endswith(".pdf")):
        raise ValueError("Nome de PDF invalido.")
    path = PDF_DIR / name
    if path.is_symlink() or path.resolve().parent != PDF_DIR.resolve():
        raise ValueError("PDF fora da pasta permitida.")
    return path


def parse_numbers(text):
    """Valida a linha inteira antes de qualquer clique; remove repeticoes."""
    if not isinstance(text, str):
        return []
    line = text.strip()
    if len(line) > 128 or not re.fullmatch(r"[1-9](?:[ ,;]+[1-9])*", line):
        return []
    return list(dict.fromkeys(map(int, re.findall(r"[1-9]", line))))


class Engine:
    """Fila persistente. Uma resposta so chega ao robo depois de salva."""

    def __init__(self):
        safe_directory(PDF_DIR)
        safe_directory(RESP_DIR)
        self.state = self._load()
        self.persisted = copy.deepcopy(self.state)
        self.save()

    def _load(self):
        state = {"jobs": []}
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                raise ValueError("Registro existente invalido; dados preservados.") from error
            if (not isinstance(state, dict) or state.get("servidor") not in
                    ("vigia-pdf", "separador") or not isinstance(state.get("jobs"), list)):
                raise ValueError("Registro existente invalido; dados preservados.")
        identifiers = set()
        for job in state["jobs"]:
            if not isinstance(job, dict):
                raise ValueError("Tarefa existente invalida; dados preservados.")
            safe_pdf(job.get("name"))
            # Marcadores antigos sem id ficam no historico, fora da fila.
            if job.get("status") == "anterior" and "id" not in job:
                continue
            if not isinstance(job.get("id"), str) or job["id"] in identifiers:
                raise ValueError("Tarefa existente invalida; dados preservados.")
            identifiers.add(job["id"])
            # Tarefa de uma execucao anterior (inclusive da antiga extensao): o robo ja nao
            # espera por ela. Fica no historico, sem reenviar.
            if job.get("status") in ("fila", "preparando", "aguardando", "enviando"):
                job.update(status="erro", error="Programa reiniciado antes da resposta.", finished=time.time())
                job.pop("tabId", None)
        state.update(servidor="separador", ok=True, code="STATE", message="Registro local do fluxo.", version=6)
        return state

    def save(self):
        with LOCK:
            try:
                write_json(STATE_PATH, self.state)
            except Exception:
                self.state = copy.deepcopy(self.persisted)
                raise
            self.persisted = copy.deepcopy(self.state)

    def capture_job(self, version):
        return next((job for job in reversed(self.state["jobs"])
                     if version and job.get("captura") == version), None)

    def receber_pdf(self, content, capture=""):
        """Grava o PDF e cria a tarefa; a mesma captura nunca gera duas tarefas."""
        if (len(content) > MAX_PDF or not content.startswith(b"%PDF-") or
                not content.rstrip().endswith(b"%%EOF")):
            raise ValueError("Conteudo PDF invalido.")
        with LOCK:
            job = self.capture_job(capture)
            if job is not None:
                return job
            if capture and (ROBOT is None or not ROBOT.atual(capture)):
                raise ValueError("Captura encerrada; nenhum PDF foi enviado.")
            safe_directory(PDF_DIR)
            path = PDF_DIR / ("imagens_numeradas_" + secrets.token_hex(8) + ".pdf")
            try:
                atomic_bytes(path, content)
                return self.create(path.name, capture)
            except Exception:
                path.unlink(missing_ok=True)
                raise

    def create(self, name, capture=""):
        with LOCK:
            path = safe_pdf(name)
            job = dict(id=secrets.token_hex(16), name=path.name, status="fila", created=time.time(),
                       captura=capture, progress="", error="", provider=PROVIDER)
            self.state["jobs"].append(job)
            self.save()
            return job

    def processar_api(self, job, jpeg):
        """Envia a pagina numerada a API da OpenAI e grava a resposta em TXT."""
        with LOCK:
            job.update(status="aguardando", sentAt=time.time(), progress="Enviado a API da OpenAI.")
            self.save()
        log("OpenAI: enviando a imagem numerada (modelo %s)." % API.modelo)
        try:
            text = API.responder(jpeg)
        except Exception as error:
            self.falha(job, str(error))
            return
        try:
            self.result(job, text)
        except ValueError as error:
            self.falha(job, str(error))

    def result(self, job, text):
        with LOCK:
            if job.get("status") == "concluido":
                return job["output"]
            if job.get("status") != "aguardando":
                raise ValueError("A tarefa nao esta aguardando resposta.")
            if (not isinstance(text, str) or not text.strip() or
                    len(text.encode("utf-8")) > MAX_RESULT):
                raise ValueError("Resposta vazia ou acima de 5 MB.")
            safe_directory(RESP_DIR)
            content = text.strip() + "\n"
            stem = Path(job["name"]).stem[:160]
            for number in range(10000):
                name = stem + ("" if number == 0 else "_" + str(number)) + ".txt"
                target = RESP_DIR / name
                try:
                    with target.open("x", encoding="utf-8", newline="\n") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    break
                except FileExistsError:
                    continue
            else:
                raise ValueError("Nao foi possivel reservar o TXT.")
            job.update(status="concluido", output="respostas/" + name,
                       finished=time.time(), progress="Resposta salva.", error="")
            self.save()
            log("Resposta da OpenAI em %.1fs, salva em pdf/respostas/%s"
                % (job["finished"] - float(job.get("created") or job["finished"]), name))
            # Uma falha na limpeza nunca impede uma resposta ja persistida.
            try:
                self.cleanup(safe_pdf(job["name"]), target)
            except OSError:
                logger.exception("Resposta salva; limpeza adiada.")
            if ROBOT is not None and job.get("captura"):
                job["entrega"] = ROBOT.deliver(content, job["captura"])
                if job["entrega"] != "enviado":
                    log("Resposta nao executada (%s)." % job["entrega"])
                if job["entrega"] == "formato_invalido":
                    # A API respondeu sem numeros: conta como falha e tira outra captura sozinho.
                    ROBOT.falhou(job["captura"])
            return job["output"]

    def falha(self, job, message):
        with LOCK:
            if job["status"] == "concluido":
                return
            job.update(status="erro", error=str(message)[:500], finished=time.time())
            self.save()
        log("Falha na API da OpenAI: " + job["error"])
        if ROBOT is not None and job.get("captura"):
            ROBOT.falhou(job["captura"])

    def cleanup(self, keep_pdf, keep_txt):
        safe_directory(PDF_DIR)
        safe_directory(RESP_DIR)
        protected = {keep_pdf.resolve(), keep_txt.resolve()}
        protected.update(safe_pdf(job["name"]).resolve() for job in self.state["jobs"]
                         if job.get("status") in ("fila", "aguardando"))
        for directory, pattern in ((PDF_DIR, "*.pdf"), (RESP_DIR, "*.txt")):
            for path in directory.glob(pattern):
                if (path.is_symlink() or not path.is_file() or
                        path.resolve().parent != directory.resolve() or path.resolve() in protected):
                    continue
                try:
                    path.unlink()
                except OSError:
                    logger.warning("Arquivo antigo ocupado; limpeza adiada.")


class Robo:
    """Captura, gera o PDF e executa a resposta, tudo no mesmo processo."""

    MAX_TENTATIVAS = 3
    MAX_REPETICOES = 2
    # Producao sem ninguem no terminal: aviso sem resposta volta a vigiar sozinho.
    RETOMAR_SOZINHO = 30.0
    # Erro inesperado no robo: espera um pouco, reabre o Chrome invisivel e recomeca.
    ESPERA_RECOMECAR = 10.0
    aviso_desde = None

    def __init__(self):
        self.inbox = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None
        self.keyboard = None
        self.version = ""
        self.waiting = False
        self.message = "Robo parado."
        self.session = secrets.token_hex(8)
        self.sequence = 0
        self.repeticoes = 0

    def status(self):
        with LOCK:
            return {"ativo": bool(self.thread and self.thread.is_alive()),
                    "aguardando": self.waiting, "versao": self.version, "message": self.message}

    def start(self):
        with LOCK:
            if self.thread and self.thread.is_alive():
                return False
            self.inbox = queue.Queue()
            self.stop_event.clear()
            self.waiting = False
            self.version = ""
            self.message = "Abrindo o robo."
            self.thread = threading.Thread(target=self.run, name="robo", daemon=True)
            self.thread.start()
            if self.keyboard is None and sys.stdin is not None and sys.stdin.isatty():
                self.keyboard = threading.Thread(target=self.read_keyboard, name="entrada-terminal", daemon=True)
                self.keyboard.start()
            return True

    def stop(self):
        self.stop_event.set()
        with LOCK:
            self.waiting = False
        self.inbox.put(("", "s"))

    def read_keyboard(self):
        for line in sys.stdin:
            command = line.strip().lower()
            if command == "s":
                self.stop()
            elif command:
                with LOCK:
                    if not self.waiting:
                        log("Aguarde: o robo esta ocupado.")
                        continue
                    self.waiting = False
                    self.inbox.put((self.version, command))

    def publish(self, content):
        """Registra uma nova captura. Nunca exibe, grava ou interpreta os pixels."""
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Captura PNG invalida.")
        with LOCK:
            self.sequence += 1
            self.version = "%s-%s" % (self.session, self.sequence)
            self.waiting = True
            self.message = "Captura pronta; aguardando a OpenAI."
            return self.version

    def atual(self, version):
        with LOCK:
            return bool(not self.stop_event.is_set() and self.waiting and version and version == self.version)

    def deliver(self, text, version):
        values = parse_numbers(text)
        with LOCK:
            if not values:
                return "formato_invalido"
            if not version or version != self.version or not self.waiting:
                return "captura_desatualizada"
            self.waiting = False
            self.repeticoes = 0
            self.message = "Resposta recebida; executando."
            line = ",".join(map(str, values))
            self.inbox.put((version, line))
        log("Resposta: " + line + " [Enter]")
        return "enviado"

    def falhou(self, version):
        """Falha na API: tira outra captura sozinho, no maximo duas vezes seguidas."""
        with LOCK:
            if not self.atual(version):
                return
            if self.repeticoes >= self.MAX_REPETICOES:
                self.aviso_desde = time.monotonic()
                log("Falhou de novo. Digite p ou os numeros (ex.: 1,5,9); sem resposta em %ds, "
                    "volto a vigiar sozinho." % self.RETOMAR_SOZINHO)
                return
            self.repeticoes += 1
            self.waiting = False
            self.inbox.put((version, "p"))
        log("Nova captura automatica (tentativa %s)." % self.repeticoes)

    def capturar(self, browser, anterior):
        """Captura e gera o PDF; repete a captura se a grade nao for reconhecida."""
        espera = None
        for tentativa in range(1, self.MAX_TENTATIVAS + 1):
            inicio = time.monotonic()
            png = browser.capture(anterior, espera)
            if png is None or self.stop_event.is_set():
                return
            version = self.publish(png)
            try:
                pdf = browser.gerar_pdf(png)
            except ValueError as error:
                if tentativa == self.MAX_TENTATIVAS:
                    raise ValueError("Grade nao reconhecida (%s). Digite p para nova captura "
                                     "ou os numeros (ex.: 1,5,9)." % error) from error
                log("Grade nao reconhecida (%s). Capturando de novo." % error)
                anterior, espera = None, 2.0
                continue
            if self.stop_event.is_set():
                return
            job = ENGINE.receber_pdf(pdf, version)
            threading.Thread(target=ENGINE.processar_api, args=(job, getattr(browser, "jpeg", "")),
                             name="openai", daemon=True).start()
            log("Captura + PDF em %.1fs; enviando a OpenAI." % (time.monotonic() - inicio))
            return

    def aviso(self, message):
        with LOCK:
            self.waiting = True
            self.message = message
            self.aviso_desde = time.monotonic()
        log(message + " (sem resposta em %ds, volto a vigiar sozinho)" % self.RETOMAR_SOZINHO)

    def retomar(self):
        """Ninguem respondeu ao aviso no terminal: em producao, volta a vigiar a tela sozinho."""
        with LOCK:
            if not (self.waiting and self.aviso_desde is not None and
                    time.monotonic() - self.aviso_desde >= self.RETOMAR_SOZINHO):
                return False
            self.waiting, self.aviso_desde = False, None
        log("Ninguem respondeu no terminal em %ds; voltando a vigiar a tela." % self.RETOMAR_SOZINHO)
        return True

    def ciclo(self, browser):
        try:
            self.capturar(browser, None)
        except ValueError as error:
            self.aviso(str(error))
        while not self.stop_event.is_set():
            try:
                version, answer = self.inbox.get(timeout=0.5)
            except queue.Empty:
                if not self.retomar():
                    continue
                version, answer = self.version, "p"
            if answer == "s":
                break
            with LOCK:
                if version != self.version:
                    continue
                self.aviso_desde = None
            try:
                anterior = None
                if answer != "p":
                    anterior = browser.answer(answer)
                self.capturar(browser, anterior)
            except ValueError as error:
                self.aviso(str(error))

    def run(self):
        """Fica de pe enquanto o programa estiver aberto: um erro inesperado (Chrome, mouse...)
        e avisado, o Chrome invisivel e reaberto e a vigilancia recomeca."""
        primeira = True
        while not self.stop_event.is_set():
            browser = None
            try:
                browser = TelaRobo(self.stop_event)
                primeira = False
                browser.open()
                self.ciclo(browser)
            except Exception as error:
                with LOCK:
                    self.message = "Robo interrompido: " + (str(error).strip().splitlines() or ["erro"])[0]
                logger.debug("Falha no robo.", exc_info=True)
                if primeira:
                    # Nem abriu (Selenium ausente, fora do Windows...): insistir nao resolve.
                    log(self.message)
                    break
                log(self.message[:300] + " Recomecando em %ds." % self.ESPERA_RECOMECAR)
            finally:
                with LOCK:
                    self.waiting = False
                if browser is not None:
                    browser.close_driver()
            if self.stop_event.wait(self.ESPERA_RECOMECAR):
                break


class TelaRobo:
    """Print da tela a cada 5s; localiza a grade e responde com cliques humanos."""

    centros = {}
    botao = None
    grade = None
    last = None
    # Depois de responder, da tempo ao desafio de trocar antes da primeira print.
    ESPERA_APOS_RESPOSTA = 2.0
    # So entrega um recorte repetido em duas prints seguidas: as imagens surgem aos poucos
    # e, logo apos a resposta, o desafio antigo ainda pode estar na tela.
    CONFIRMAR_TELA = 1.0
    # Chrome invisivel que parou de responder (processo fechado, falta de memoria...):
    # 2 falhas seguidas reabrem o Chrome; desiste apos 3 reaberturas sem voltar a funcionar.
    FALHAS_PARA_REABRIR = 2
    MAX_REINICIOS = 3
    # Programa aberto por muito tempo: renova o Chrome invisivel a cada 2h, so sem desafio na
    # tela, antes que ele acumule memoria e caia.
    RENOVAR_CHROME = 2 * 3600.0
    aberto_em = None
    # Mesmo desafio parado depois da resposta (clique nao pegou, erro do site): responde de novo.
    REPETIR_MESMO = 15.0
    # Grade nas mesmas posicoes, mas com algum detalhe mudando a cada print (aviso, animacao).
    MAX_MUDANCAS = 3

    def __init__(self, stop_event):
        if os.name != "nt":
            raise RuntimeError("O programa so funciona no Windows (print da tela e mouse).")
        self.driver = self._abrir_chrome()
        self.aberto_em = time.monotonic()
        self.stop_event = stop_event
        self.centros = {}
        self.botao = None

    @staticmethod
    def _abrir_chrome():
        """Chrome invisivel so para processar imagens (Selenium; o resto e biblioteca padrao)."""
        try:
            from selenium import webdriver
        except ImportError as error:
            raise RuntimeError("Selenium ausente. Execute INSTALAR.bat.") from error
        options = webdriver.ChromeOptions()
        for argument in ("--headless=new", "--disable-gpu", "--disable-extensions", "--no-first-run"):
            options.add_argument(argument)
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
        return driver

    def open(self):
        """Nao abre site: o Chrome em branco so executa o JS de processamento."""
        self.driver.get("about:blank")
        log("Chrome invisivel em branco, usado apenas para processar a imagem da tela.")

    def reiniciar_chrome(self, motivo, parou=True):
        """Fecha o Chrome invisivel e abre outro, sem o usuario precisar agir."""
        log(("Chrome invisivel parou (%s); abrindo de novo." if parou else "Renovando o Chrome invisivel (%s).")
            % motivo[:160])
        self.close_driver()
        try:
            self.driver = self._abrir_chrome()
            self.driver.get("about:blank")
        except Exception as error:
            log("Nao consegui abrir o Chrome invisivel de novo: %s" % primeira_linha(error)[:200])
            return False
        self.aberto_em = time.monotonic()
        if parou:
            log("Chrome invisivel aberto de novo; vigiando a tela.")
        return True

    def gerar_pdf(self, png):
        """Gera o PDF numerado no Chrome invisivel, sem abrir pagina. Se o Chrome tiver
        caido bem nessa hora, reabre e tenta mais uma vez."""
        fonte = base64.b64encode(png).decode("ascii")
        for tentativa in range(2):
            try:
                value = self.driver.execute_async_script(GERAR_PDF_JS, fonte)
                break
            except Exception as error:
                if tentativa == 0 and self.reiniciar_chrome(primeira_linha(error)):
                    continue
                raise ValueError("Falha ao gerar o PDF: " + primeira_linha(error)) from error
        if not isinstance(value, dict) or not value.get("ok"):
            raise ValueError((value or {}).get("message") or "Falha ao gerar o PDF.")
        if value.get("completada"):
            log("Linhas da grade montadas pelas posicoes fixas do 3x3.")
        self.jpeg = value.get("jpeg", "")
        return base64.b64decode(value["pdf"])

    def close_driver(self):
        try:
            self.driver.quit()
        except Exception:
            pass

    def capture(self, anterior=None, espera=None):
        """Espera a grade 3x3 aparecer na tela e devolve o recorte do desafio."""
        # Uma captura que falhe ou seja interrompida invalida as coordenadas anteriores.
        self.centros, self.botao = {}, None
        # Depois de responder, da tempo ao desafio de trocar antes da primeira print.
        if espera is None:
            espera = self.ESPERA_APOS_RESPOSTA if anterior else 0.0
        if espera and self.stop_event.wait(espera):
            return None
        inicio = time.monotonic()
        ultimo = candidato = repetido = mesmo_desde = None
        falhas_js, reinicios, tela_falhou, mudancas = 0, 0, False, 0
        while not self.stop_event.is_set() and (
                TELA_TIMEOUT <= 0 or time.monotonic() - inicio < TELA_TIMEOUT):
            pausa = INTERVALO_TELA
            try:
                png, ox, oy = png_tela()
            except (OSError, RuntimeError) as error:
                # Tela bloqueada ou protetor de tela: avisa uma vez e continua vigiando.
                if not tela_falhou:
                    log("Falha ao capturar a tela (%s); tentando de novo." % error)
                tela_falhou, candidato = True, None
                self.stop_event.wait(INTERVALO_TELA)
                continue
            tela_falhou = False
            digest = hashlib.sha256(png).digest()
            if digest == ultimo:
                # Tela identica a print anterior: o candidato ficou estavel.
                if candidato is not None:
                    return self.entregar(*candidato)
                if repetido is not None and time.monotonic() - mesmo_desde >= self.REPETIR_MESMO:
                    log("O desafio nao mudou em %ds; respondendo de novo." % self.REPETIR_MESMO)
                    return self.entregar(*repetido)
            else:
                # Sem desafio em andamento e Chrome aberto ha muito tempo: renova antes que caia.
                if self.aberto_em is None:
                    self.aberto_em = time.monotonic()
                if (candidato is None and repetido is None and
                        time.monotonic() - self.aberto_em >= self.RENOVAR_CHROME):
                    self.reiniciar_chrome("aberto ha %dh" % (self.RENOVAR_CHROME // 3600), parou=False)
                try:
                    value = self.driver.execute_async_script(
                        LOCALIZAR_JS, base64.b64encode(png).decode("ascii"))
                    falhas_js = reinicios = 0
                except Exception as error:
                    # Chrome invisivel parado: reabre sozinho; nunca vigiar em silencio para sempre.
                    falhas_js += 1
                    motivo = primeira_linha(error)
                    logger.debug("Falha ao localizar a grade na tela: %s", error)
                    if falhas_js >= self.FALHAS_PARA_REABRIR:
                        if reinicios >= self.MAX_REINICIOS:
                            raise ValueError("O Chrome invisivel parou %d vezes seguidas (%s). Feche e abra o "
                                             "programa." % (reinicios, motivo[:160])) from error
                        reinicios += 1
                        self.reiniciar_chrome(motivo)
                        falhas_js = 0
                    value = None
                # So marca a print como processada se o JS respondeu; uma falha
                # temporaria deve permitir tentar de novo na proxima rodada.
                if isinstance(value, dict):
                    ultimo = digest
                if isinstance(value, dict) and not value.get("ok"):
                    self.avisar(str(value.get("message") or "grade nao encontrada"))
                    repetido = mesmo_desde = None
                novo = None
                if isinstance(value, dict) and value.get("ok") and value.get("png"):
                    try:
                        recorte = base64.b64decode(value["png"])
                        centros = {int(n): (int(x) + ox, int(y) + oy)
                                   for n, (x, y) in value["cells"].items()}
                        botao = (int(value["button"][0]) + ox, int(value["button"][1]) + oy)
                        g = value.get("grade")
                        grade = (float(g[0]) + ox, float(g[1]) + ox, float(g[2]) + oy, float(g[3])) if g else None
                    except (KeyError, TypeError, ValueError, IndexError) as error:
                        logger.debug("Resposta invalida ao localizar a grade: %s", error)
                    else:
                        lido = (recorte, centros, botao, bool(value.get("botaoAchado")), grade)
                        if recorte != anterior:
                            novo, repetido, mesmo_desde = lido, None, None
                        else:
                            # O desafio ja respondido continua na tela: espera trocar, mas nao para sempre.
                            if repetido is None:
                                mesmo_desde = time.monotonic()
                                log("O mesmo desafio continua na tela; se nao mudar em %ds, respondo de novo."
                                    % self.REPETIR_MESMO)
                            repetido = lido
                            if time.monotonic() - mesmo_desde >= self.REPETIR_MESMO:
                                log("O desafio nao mudou em %ds; respondendo de novo." % self.REPETIR_MESMO)
                                return self.entregar(*lido)
                if novo is not None and novo == candidato:
                    return self.entregar(*novo)
                if novo is not None and candidato is not None and novo[1] == candidato[1]:
                    # Mesmas 9 posicoes, so algum detalhe mudando: nao esperar para sempre.
                    mudancas += 1
                    if mudancas >= self.MAX_MUDANCAS:
                        log("A grade esta parada, mas algum detalhe muda a cada print; respondendo assim mesmo.")
                        return self.entregar(*novo)
                else:
                    mudancas = 0
                candidato = novo
                if candidato is not None:
                    pausa = self.CONFIRMAR_TELA
            self.stop_event.wait(pausa)
        if not self.stop_event.is_set() and TELA_TIMEOUT > 0:
            raise ValueError("A imagem nao apareceu na tela em %s segundos. Digite p para tentar outra captura."
                             % int(TELA_TIMEOUT))
        return None

    AVISO_TELA = 20.0

    def avisar(self, motivo):
        """Diz no terminal por que a grade nao foi aceita, no maximo a cada 20s e sem repetir."""
        agora = time.monotonic()
        anterior, quando = getattr(self, "aviso_tela", ("", None))
        if motivo != anterior and (quando is None or agora - quando >= self.AVISO_TELA):
            self.aviso_tela = (motivo, agora)
            log("Vigiando a tela: " + motivo)

    def entregar(self, recorte, centros, botao, achado=False, grade=None):
        log("Botao de confirmar: %s." % ("achado na tela" if achado else "estimado pela proporcao do desafio"))
        self.centros, self.botao, self.last, self.grade = centros, botao, recorte, grade
        return recorte

    def botao_atual(self):
        """Procura o botao de novo depois de marcar as imagens: o texto e a largura dele
        mudam (Pular -> Verificar/Proximo) e o centro muda junto. Sem achar, usa o da captura."""
        grade, driver = getattr(self, "grade", None), getattr(self, "driver", None)
        if not grade or driver is None:
            return self.botao
        try:
            png, ox, oy = png_tela()
            value = driver.execute_async_script(BOTAO_JS, base64.b64encode(png).decode("ascii"),
                                                [grade[0] - ox, grade[1] - ox, grade[2] - oy, grade[3]])
            if isinstance(value, dict) and value.get("ok"):
                botao = (int(value["button"][0]) + ox, int(value["button"][1]) + oy)
                if botao != self.botao:
                    log("Botao de confirmar: reposicionado depois de marcar as imagens.")
                return botao
        except Exception as error:
            logger.debug("Falha ao procurar o botao de novo: %s", error)
        return self.botao

    def answer(self, text):
        """Clica nos numeros e confirma com pausas humanas, sem tocar o navegador."""
        values = parse_numbers(text)
        if text != "v" and not values:
            raise ValueError("Use numeros de 1 a 9, p, v ou s.")
        if not self.centros or self.botao is None:
            raise ValueError("A imagem nao esta na tela. Digite p para tentar outra captura.")
        if any(number not in self.centros for number in values):
            raise ValueError("Faltam coordenadas para a resposta. Nenhum clique executado.")
        for number in values:
            if self.stop_event.is_set():
                return self.last
            clique_humano(*self.centros[number], self.stop_event)
            if self.stop_event.wait(random.uniform(0.15, 0.35)):
                return self.last
        if self.stop_event.wait(0.5):
            return self.last
        botao = self.botao_atual()
        if not self.stop_event.is_set():
            clique_humano(*botao, self.stop_event)
        return self.last


def chave_openai():
    """Chave da variavel OPENAI_API_KEY ou do arquivo chave_openai.txt (nunca exibida)."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key and CHAVE_PATH.is_file():
        key = CHAVE_PATH.read_text(encoding="utf-8").strip()
    return key if re.fullmatch(r"sk-[A-Za-z0-9_\-]{20,}", key) else ""


class ClienteOpenAI:
    """API de respostas da OpenAI, somente com a biblioteca padrao."""

    def __init__(self, key, modelo):
        self.key = key
        self.modelo = modelo

    def responder(self, jpeg_b64):
        if not jpeg_b64:
            raise ValueError("Imagem da pagina ausente.")
        body = json.dumps({"model": self.modelo, "input": [{"role": "user", "content": [
            {"type": "input_text", "text": PROMPT_API},
            {"type": "input_image", "image_url": "data:image/jpeg;base64," + jpeg_b64, "detail": "high"}]}]}).encode("utf-8")
        data = None
        for tentativa in range(3):
            request = urllib.request.Request(OPENAI_URL, data=body, method="POST", headers={
                "Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503, 504) and tentativa < 2:
                    time.sleep(2 * (tentativa + 1))
                    continue
                try:
                    detalhe = json.loads(error.read().decode("utf-8")).get("error", {}).get("message", "")
                except (ValueError, OSError, AttributeError):
                    detalhe = ""
                raise ValueError("API da OpenAI recusou (%s): %s" % (error.code, (detalhe or str(error.reason))[:200])) from None
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if tentativa < 2:
                    time.sleep(2)
                    continue
                raise ValueError("Sem conexao com a API da OpenAI: %s" % getattr(error, "reason", error)) from None
        text = "".join(part.get("text", "") for item in (data or {}).get("output", []) if item.get("type") == "message"
                       for part in item.get("content", []) if part.get("type") == "output_text").strip()
        if not text:
            raise ValueError("A API da OpenAI respondeu sem texto.")
        uso = (data or {}).get("usage") or {}
        if uso:
            log("OpenAI: %s tokens de entrada + %s de saida." % (uso.get("input_tokens", "?"), uso.get("output_tokens", "?")))
        return text


class InstanceLock:
    """Impede abrir o programa duas vezes (dois robos clicando na mesma tela)."""

    def __enter__(self):
        safe_directory(LOCK_PATH.parent)
        self.stream = LOCK_PATH.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            raise RuntimeError("O programa ja esta aberto em outro terminal.") from error
        return self

    def __exit__(self, *args):
        self.stream.close()


def main(argv=None):
    global ENGINE, ROBOT, API
    parser = argparse.ArgumentParser(description="Print da tela -> PDF -> API da OpenAI -> cliques, tudo no terminal.")
    parser.add_argument("--modelo", default=os.environ.get("OPENAI_MODEL", MODELO_PADRAO), help="modelo da API da OpenAI")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    # Avisos internos de nova tentativa do Selenium ("Retrying ...") so poluiam o terminal;
    # o programa ja avisa e reabre o Chrome invisivel quando ele para.
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    key = chave_openai()
    if not key:
        print("Coloque a chave da OpenAI (sk-...) em chave_openai.txt ou na variavel OPENAI_API_KEY.",
              file=sys.stderr)
        return 1
    try:
        with InstanceLock():
            API = ClienteOpenAI(key, args.modelo)
            ENGINE = Engine()
            ROBOT = Robo()
            try:
                log("Programa pronto (versao %s). Comandos: numeros (1,5,9) | p nova captura | v verificar | "
                    "s ou Ctrl+C sair." % VERSAO)
                log("Print da tela a cada %gs; respostas pela API da OpenAI (modelo %s)." % (INTERVALO_TELA, args.modelo))
                signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
                ROBOT.start()
                # O robo roda em outra thread; `s` ou Ctrl+C encerram o programa.
                while ROBOT.thread.is_alive():
                    ROBOT.thread.join(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                ROBOT.stop()
                if ROBOT.thread is not None:
                    ROBOT.thread.join(5)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
