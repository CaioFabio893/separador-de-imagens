#!/usr/bin/env python3
"""Programa unico, todo no terminal: captura -> PDF numerado -> ChatGPT Web -> TXT -> robo.

Nao abre interface. O PDF e gerado no proprio navegador do robo (Selenium), sem
exibir nada; a extensao do Chrome envia o PDF ao ChatGPT Web e devolve a resposta.
O servidor usa apenas a biblioteca padrao e escuta somente em 127.0.0.1.
"""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import hmac
import json
import logging
import os
import queue
import re
import secrets
import signal
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = Path(__file__).resolve().parent
PDF_DIR = APP_DIR / "pdf"
RESP_DIR = PDF_DIR / "respostas"
STATE_PATH = PDF_DIR / ".fluxo.json"
LEGACY_STATE_PATH = PDF_DIR / ".vigia_deepseek.json"
CONFIG_PATH = PDF_DIR / "extensao_deepseek" / "config_local.js"
PROFILE_DIRECTORY = "Default"
MAX_PDF = MAX_PAYLOAD = 20 * 1024 * 1024
MAX_RESULT = 5 * 1024 * 1024
MAX_ESPERA = 25
LOCK = threading.RLock()
NOVA_TAREFA = threading.Condition(LOCK)
logger = logging.getLogger("separador")
PROVIDER = "chatgpt_web"
VERSAO = "5.0.0"
SECRET = ""
ENGINE = ROBOT = None


def log(message):
    print(time.strftime("[%H:%M:%S] ") + message, flush=True)


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
  if (opt.whiteThr === 244) attempts.push([230, thinGap, 0.84]);
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
    return {ok: true, pdf: btoa(binary), completada};
  } finally { image.close(); }
})().then(concluir, error => concluir({ok: false, message: String((error && error.message) || error)}));
'''

EXT_BACKGROUND = r'''"use strict";
importScripts("config_local.js");
const PORTS = Array.from({length: 20}, (_, index) => 8765 + index);
const PROMPT = "Responda ao PDF anexado seguindo as instruções contidas nele. Preserve a numeração solicitada. Não faça pesquisas na web.";
const PROVIDER = "chatgpt_web";
const ESPERA = 20;
const result = (ok, code, message) => ({servidor: "separador", ok, code, message});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const taskKey = id => "chatgpt-task-" + id;
let busy = false, looping = false, base = "", lastProgress = "";

function validBase(value) {
  return typeof value === "string" && /^http:\/\/127\.0\.0\.1:\d{4,5}$/.test(value);
}
async function probe(candidate) {
  const response = await fetch(candidate + "/api/status", {signal: AbortSignal.timeout(1500)});
  const value = await response.json();
  return response.ok && value.ok && value.servidor === "separador" && value.provedor === PROVIDER;
}
async function findBase() {
  if (validBase(base)) return base;
  const attempts = await Promise.allSettled(PORTS.map(async port => {
    const candidate = "http://127.0.0.1:" + port;
    if (await probe(candidate)) return candidate;
    throw Error("nao e o separador");
  }));
  const found = attempts.find(item => item.status === "fulfilled");
  if (!found) throw Error("Programa local fechado. Abra abrir_separador.bat.");
  base = found.value;
  return base;
}
async function api(path, data, timeout = 15000) {
  for (let attempt = 0; ; attempt++) {
    const root = await findBase();
    let response;
    try {
      response = await fetch(root + path, {
        method: data ? "POST" : "GET",
        headers: {"X-Vigia-Token": VIGIA_TOKEN, "X-Vigia-Provider": PROVIDER, "X-Vigia-Extension": chrome.runtime.id,
          "X-Vigia-Version": chrome.runtime.getManifest().version, ...(data ? {"Content-Type": "application/json"} : {})},
        body: data ? JSON.stringify(data) : undefined,
        signal: AbortSignal.timeout(timeout)
      });
    } catch (error) {
      base = "";
      if (attempt >= 1) throw error;
      continue;
    }
    const value = await response.json();
    if (value.provedor !== PROVIDER) throw Error("Reinicie o programa atualizado para usar o ChatGPT Web.");
    if (!response.ok || !value.ok) throw Error(value.message || "Falha no fluxo local.");
    return value;
  }
}
async function badge(text, title) {
  await chrome.action.setBadgeText({text});
  await chrome.action.setTitle({title});
}
async function tabAlive(id) {
  if (typeof id !== "number") return false;
  try { await chrome.tabs.get(id); return true; } catch { return false; }
}
async function ownedTab(job) {
  const saved = (await chrome.storage.local.get(taskKey(job.id)))[taskKey(job.id)];
  return saved?.tabId === job.tabId && await tabAlive(job.tabId);
}
async function preparing(job, message) {
  await badge("…", message);
  if (message === lastProgress) return;
  lastProgress = message;
  await api("/api/vigia/progress", {id: job.id, message}).catch(() => {});
}
async function connectTab(job) {
  const ping = result(true, "PING", "Verificar ChatGPT.");
  try { return await chrome.tabs.sendMessage(job.tabId, ping); } catch {}
  if (!(await ownedTab(job))) throw Error("A aba nao pertence a esta tarefa.");
  const tab = await chrome.tabs.get(job.tabId);
  if (!tab.url?.startsWith("https://chatgpt.com/")) throw Error("Aguardando a aba terminar de abrir o ChatGPT.");
  // Recupera uma aba que abriu antes de o script da extensao estar disponivel.
  await chrome.scripting.executeScript({target: {tabId: job.tabId}, files: ["chatgpt.js"]});
  return await chrome.tabs.sendMessage(job.tabId, ping);
}
async function openTab() {
  const url = "https://chatgpt.com/";
  const windows = await chrome.windows.getAll({windowTypes: ["normal"]});
  windows.sort((a, b) => Number(!!b.focused) - Number(!!a.focused));
  for (const window of windows) {
    // Aba ativa: o Chrome nao desacelera o script nem a renderizacao da resposta.
    try { return await chrome.tabs.create({windowId: window.id, url, active: true}); } catch {}
  }
  // Nunca cria uma janela do Chrome: o programa abre o Chrome quando necessario.
  return null;
}
async function checkVersion(value) {
  // Depois de atualizar o programa, recarrega a extensao sozinha (somente sem tarefa ativa).
  const wanted = value.extensao_versao;
  if (!wanted || wanted === chrome.runtime.getManifest().version) return;
  const last = (await chrome.storage.local.get("reload-at"))["reload-at"] || 0;
  if (Date.now() - last < 60000) return;
  await chrome.storage.local.set({"reload-at": Date.now()});
  chrome.runtime.reload();
}

async function tick(espera = 0) {
  if (busy) return "busy";
  busy = true;
  let job, fatal = false;
  try {
    const next = await api("/api/vigia/next" + (espera ? "?espera=" + espera : ""), null, (espera + 10) * 1000);
    job = next.job;
    if (job && job.provider !== PROVIDER) throw Error("Tarefa de outra versao. Reinicie o programa atualizado.");
    if (!job) {
      await badge("", "Fluxo PDF: aguardando um PDF novo.");
      await checkVersion(next);
      return "idle";
    }
    if (job.status === "aguardando") {
      if (!(await ownedTab(job))) {
        fatal = true;
        throw Error("A aba do ChatGPT foi fechada antes da resposta. O PDF pode já ter sido recebido.");
      }
      try {
        await connectTab(job);
        const resumed = await chrome.tabs.sendMessage(job.tabId, {
          ...result(true, "RESUME", "Retomar coleta sem reenviar PDF."), provider: PROVIDER, id: job.id, prompt: PROMPT
        });
        if (resumed?.code !== "BUSY") await preparing(job, resumed?.message || "Aguardando resposta na aba da tarefa.");
      } catch {
        await preparing(job, "Coleta desconectada. Mantenha a aba da tarefa aberta; a coleta sera retomada sem reenviar o PDF.");
      }
      return "wait";
    }
    // Nada foi enviado ainda: se a aba sumiu, basta abrir outra.
    if (job.status === "preparando" && !(await ownedTab(job))) job = {...job, status: "fila"};
    if (job.status === "fila") {
      const saved = (await chrome.storage.local.get(taskKey(job.id)))[taskKey(job.id)];
      const tab = saved && await tabAlive(saved.tabId) ? {id: saved.tabId} : await openTab();
      if (!tab?.id) { await badge("…", "PDF na fila: abra uma janela do Chrome."); return "wait"; }
      await chrome.storage.local.set({[taskKey(job.id)]: {tabId: tab.id}});
      await chrome.tabs.update(tab.id, {autoDiscardable: false});
      await api("/api/vigia/opened", {id: job.id, tabId: tab.id});
      job = {...job, status: "preparando", tabId: tab.id};
    }
    let ready;
    try {
      ready = await connectTab(job);
    } catch {
      await preparing(job, "Abrindo o ChatGPT na aba da tarefa.");
      return "wait";
    }
    if (!ready?.ok || ready.provider !== PROVIDER) {
      await preparing(job, ready?.message || "Campo de mensagem indisponivel. Entre na conta do ChatGPT na aba aberta.");
      return "wait";
    }
    fatal = true;
    const payload = await api("/api/vigia/payload", {id: job.id});
    await api("/api/vigia/dispatch", {id: job.id});
    const accepted = await chrome.tabs.sendMessage(job.tabId, {
      ...result(true, "PROCESS", "Processar PDF."), provider: PROVIDER, id: job.id, name: payload.name,
      data: payload.data, prompt: PROMPT
    });
    if (!accepted?.ok) throw Error(accepted?.message || "O envio não foi confirmado.");
    await badge("…", "ChatGPT processando o PDF.");
    return "wait";
  } catch (error) {
    // Problemas passageiros antes do envio so tentam de novo; depois do envio, registra a falha.
    if (job && fatal) try { await api("/api/vigia/error", {id: job.id, message: error.message}); } catch {}
    await badge("!", "Fluxo: " + error.message).catch(() => {});
    return "error";
  } finally {
    busy = false;
  }
}

// Fila sem atraso: o servidor segura o pedido ate surgir um PDF (long-poll).
async function loop() {
  if (looping) return;
  looping = true;
  let failures = 0;
  try {
    while (failures < 10) {
      // Chamar uma API da extensao mantem o service worker acordado.
      await chrome.runtime.getPlatformInfo();
      const state = await tick(ESPERA);
      failures = state === "error" ? failures + 1 : 0;
      if (state !== "idle") await sleep(state === "error" ? 3000 : 700);
    }
  } finally { looping = false; }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || message?.servidor !== "separador" ||
      !sender.tab || !sender.url?.startsWith("https://chatgpt.com/")) return;
  (async () => {
    if (message.code === "RESULT") {
      if (message.provider !== PROVIDER || !(await ownedTab({id: message.id, tabId: sender.tab.id}))) {
        respond(result(false, "NOT_ACTIVE", "A aba nao pertence a esta tarefa."));
        return;
      }
      // O servidor confere tarefa + aba e nunca grava a mesma resposta duas vezes.
      const saved = await api("/api/vigia/result", {id: message.id, tabId: sender.tab.id, text: message.text});
      respond(saved);
      await chrome.tabs.remove(sender.tab.id).catch(() => {});
      await chrome.storage.local.remove(taskKey(message.id));
      void loop();
      return;
    }
    const job = (await api("/api/vigia/next")).job;
    if (message.provider !== PROVIDER || !job || job.id !== message.id || job.tabId !== sender.tab.id || !(await ownedTab(job))) {
      respond(result(false, "NOT_ACTIVE", "Tarefa não está ativa."));
      return;
    }
    if (message.code === "ERROR") {
      await api("/api/vigia/error", {id: job.id, message: message.message});
      respond(result(true, "ACK", "Falha registrada; PDF preservado."));
      await badge("!", "Um PDF falhou. Os próximos continuam automaticamente.");
      return;
    }
    if (message.code === "PROGRESS") {
      lastProgress = String(message.message || "");
      try { await api("/api/vigia/progress", {id: message.id, message: message.message}); } catch {}
      respond(result(true, "ACK", "Processamento em andamento."));
    }
  })().catch(error => respond(result(false, "FAILED", error.message)));
  return true;
});

async function forgetTabs() {
  // Ids de abas nao valem depois que o Chrome reinicia.
  const values = await chrome.storage.local.get(null);
  const keys = Object.keys(values).filter(key => key.startsWith("chatgpt-task-"));
  if (keys.length) await chrome.storage.local.remove(keys);
}
async function initialize() {
  await chrome.alarms.create("fluxo-check", {periodInMinutes: 0.5});
  void loop();
}
chrome.runtime.onInstalled.addListener(() => void initialize());
chrome.runtime.onStartup.addListener(() => void forgetTabs().finally(initialize));
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "fluxo-check") void loop(); });
chrome.windows.onCreated.addListener(() => void loop());
chrome.tabs.onUpdated.addListener((_id, change, tab) => {
  if (change.status === "complete" && tab.url?.startsWith("https://chatgpt.com/")) void loop();
});
chrome.action.onClicked.addListener(() => void loop());
void initialize();
'''

EXT_CHATGPT = r'''"use strict";
(() => {
  if (globalThis.__separadorChatgptLoaded) return;
  globalThis.__separadorChatgptLoaded = true;
  const APP = "separador", PROVIDER = "chatgpt_web";
  const reply = (ok, code, message, extra = {}) => ({servidor: APP, ok, code, message, ...extra});
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  let running = false, current = null;
  const visible = node => !!node && node.isConnected && !node.hidden && node.getClientRects().length > 0;
  const textOf = node => (node?.innerText || node?.textContent || "").trim();
  const normalized = text => String(text || "").replace(/\s+/g, " ").trim();
  const REASONING = '[data-message-type="reasoning"], [data-testid*="reasoning"], [data-testid*="thinking"]';
  const USER = '[data-message-author-role="user"], article[data-turn="user"]';
  function sentPrompt(prompt) {
    return Array.from(document.querySelectorAll(USER)).some(node => normalized(textOf(node)).includes(normalized(prompt)));
  }
  function editor() {
    const primary = Array.from(document.querySelectorAll('#prompt-textarea, [data-testid="prompt-textarea"]')).find(visible);
    if (primary) return primary;
    const scope = document.querySelector("main") || document;
    return Array.from(scope.querySelectorAll('textarea[placeholder], [contenteditable="true"][role="textbox"], .ProseMirror[contenteditable="true"], [contenteditable="true"][data-lexical-editor], [contenteditable="true"][data-slate-editor]'))
      .find(node => visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true") || null;
  }
  const draftOf = input => input?.tagName === "TEXTAREA" ? input.value : textOf(input);
  const SEND_SELECTOR = 'button[data-testid="send-button"], button#composer-submit-button, button[aria-label="Send prompt"], button[aria-label="Send message"], button[aria-label="Send"], button[aria-label="Enviar mensagem"], button[aria-label="Enviar prompt"], button[aria-label="Enviar"]';
  function composer() {
    const input = editor();
    if (!input) return null;
    // O editor pode estar em um bloco interno, separado do botao e do anexo.
    const form = input.closest("form");
    if (form) return form;
    let scope = input.closest('[data-type="unified-composer"], [data-testid="composer"]') || input.parentElement;
    const fallback = scope;
    for (let depth = 0; scope && depth < 5; depth++, scope = scope.parentElement) {
      if (scope.matches("main, body, html")) break;
      if (scope.querySelector(SEND_SELECTOR)) return scope;
    }
    return fallback;
  }
  function fileInputs() {
    // Campos que aceitam PDF; os da caixa de mensagem primeiro.
    const box = composer();
    return Array.from(document.querySelectorAll('input[type="file"]')).filter(node => {
      const accept = (node.getAttribute("accept") || "").toLowerCase();
      return !node.disabled && (!accept || accept.includes(".pdf") || accept.includes("application/pdf") ||
        accept.includes("application/*") || accept === "*" || accept.includes("*/*"));
    }).sort((a, b) => Number(!!box?.contains(b)) - Number(!!box?.contains(a)));
  }
  const fileInput = () => fileInputs()[0] || null;
  function disabled(node) {
    return !node || node.disabled || node.getAttribute("aria-disabled") === "true";
  }
  function sendButton() {
    const scope = composer();
    const buttons = scope ? Array.from(scope.querySelectorAll(SEND_SELECTOR)).filter(visible) : [];
    return buttons.find(node => !disabled(node)) || buttons[0] || null;
  }
  function generating() {
    return Array.from(document.querySelectorAll('button[data-testid="stop-button"], button[aria-label="Stop generating"], button[aria-label="Stop streaming"], button[aria-label="Parar de gerar"], button[aria-label="Parar geração"], button[aria-label="Interromper"], [data-is-streaming="true"], .result-streaming')).some(visible);
  }
  function assistantMessages() {
    return Array.from(document.querySelectorAll('[data-message-author-role="assistant"], article[data-turn="assistant"]'))
      .filter(node => visible(node) && !node.querySelector('[data-message-author-role="assistant"]'));
  }
  const CONTROLS = 'button[data-testid="copy-turn-action-button"], button[data-testid="good-response-turn-action-button"], button[data-testid="bad-response-turn-action-button"], button[aria-label="Copy"], button[aria-label="Copiar"], button[aria-label="Copy response"], button[aria-label="Copiar resposta"], button[title="Copy"], button[title="Copiar"]';
  // Barra de acoes da resposta (copiar, curtir...), mesmo que o ChatGPT mude os atributos.
  function actionButtons() {
    const scope = document.querySelector?.("main") || document.body || document;
    return Array.from(scope.querySelectorAll("button")).filter(node => visible(node) && (node.matches(CONTROLS) ||
      /^(copy|copiar|good response|bad response|boa resposta|resposta ruim|read aloud|ler em voz alta)/i.test(node.getAttribute("aria-label") || "")));
  }
  function cleanText(node) {
    const clone = node.cloneNode(true);
    clone.querySelectorAll(REASONING + ', button, nav, [role="toolbar"], script, style').forEach(item => item.remove());
    return textOf(clone);
  }
  function answerByControls(prompt) {
    // Sobe a partir da ultima barra de acoes ate o primeiro bloco com texto; nunca usa o bloco do prompt.
    const button = actionButtons().at(-1);
    const stop = document.querySelector?.("main") || document.body;
    for (let node = button?.parentElement; node && node !== stop; node = node.parentElement) {
      const text = cleanText(node);
      if (!text) continue;
      // Bloco da mensagem enviada (prompt ou nome do PDF): ainda nao ha resposta.
      if ((prompt && normalized(text).includes(normalized(prompt))) || /\.pdf\b/i.test(text)) return null;
      return {text, finished: !generating()};
    }
    return null;
  }
  // Mensagem enviada: aparece no historico, a conversa ganhou endereco, a geracao comecou
  // ou a caixa esvaziou e o anexo saiu dela.
  function messageSent(prompt, name) {
    return sentPrompt(prompt) || /^\/c\//.test(location.pathname || "") || generating() ||
      (!!editor() && !draftOf(editor()).trim() && !!name && !attachmentPresent(name));
  }
  function lastMarkdown(prompt) {
    // Ultimo bloco de texto formatado da pagina que nao seja a mensagem enviada.
    const scope = document.querySelector?.("main") || document.body;
    const blocks = scope ? Array.from(scope.querySelectorAll(".markdown")).filter(node => visible(node) && !node.closest(REASONING)) : [];
    const node = blocks.at(-1);
    if (!node || node.closest(USER)) return null;
    const text = textOf(node);
    if (!text || (prompt && normalized(text).includes(normalized(prompt)))) return null;
    return node;
  }
  const NUMERICA = /^[1-9](?:\s*[,;]?\s*[1-9])*\.?$/;
  function numericAnswer() {
    // Procura pelo formato (1 a 9 separados por virgula), sem depender dos atributos da pagina.
    // So dentro da conversa (main); ignora a caixa de mensagem e a barra lateral.
    const scope = document.querySelector?.("main") || document.body;
    if (!scope) return null;
    const box = composer();
    const own = node => (node.textContent || "").trim();
    const found = Array.from(scope.querySelectorAll("div, p, span, li, strong, code")).filter(node =>
      NUMERICA.test(own(node)) && visible(node) && !box?.contains(node) &&
      !node.closest('[contenteditable="true"], textarea, button, nav, aside, ' + USER) &&
      !Array.from(node.children).some(child => own(child) === own(node)));
    const node = found.at(-1);
    return node ? {text: own(node), finished: false} : null;
  }
  function finalAnswer() {
    const message = assistantMessages().at(-1) || lastMarkdown(current?.prompt);
    if (!message) return numericAnswer() || answerByControls(current?.prompt);
    if (message.closest(REASONING)) return null;
    const markdown = Array.from(message.querySelectorAll(".markdown"));
    const candidates = markdown.length ? markdown : Array.from(message.querySelectorAll('[data-testid="message-content"], [data-message-content], .prose'));
    const blocks = candidates.filter(node => visible(node) &&
      !node.closest(REASONING) && !node.querySelector(REASONING) &&
      !candidates.some(parent => parent !== node && parent.contains(node)));
    let text = blocks.map(textOf).filter(Boolean).join("\n").trim();
    if (!text && !candidates.length) {
      // Respostas simples podem nao ter markdown; exclui controles e pensamento.
      text = cleanText(message);
    }
    if (!text) return null;
    const turn = message.closest('[data-testid^="conversation-turn-"], article') || message;
    // A barra de acoes pode ficar dentro do bloco ou logo depois dele (interface nova).
    const after = node => !!(message.compareDocumentPosition(node) & 4);
    const finished = !generating() && (Array.from(turn.querySelectorAll(CONTROLS)).some(visible) || actionButtons().some(after));
    return {text, finished};
  }
  function blockingNotice() {
    // Examina somente avisos de erro; nunca copia o historico para os registros.
    const notices = Array.from(document.querySelectorAll('[role="alert"], [data-testid="error-message"], [data-testid="conversation-turn-error"]'));
    const hasError = notices.some(node => visible(node) && /limit|limite|too many|upload failed|falha|unable to upload|something went wrong|algo deu errado|try again|tente novamente|unsupported|não suportado|nao suportado/i.test(textOf(node)));
    return hasError ? "O ChatGPT exibiu um aviso de limite ou falha. Confira a aba; nao houve reenvio automatico." : "";
  }
  function send(code, message, extra = {}) {
    return chrome.runtime.sendMessage(reply(true, code, message, {id: current.id, provider: PROVIDER, ...extra}));
  }
  // Acorda a cada mudanca na pagina (sem esperar o relogio) ou, no maximo, a cada 400 ms.
  function pageChange(ms) {
    return new Promise(resolve => {
      let observer = null, timer = null;
      const finish = () => { if (timer !== null) clearTimeout(timer); observer?.disconnect(); resolve(); };
      timer = setTimeout(finish, ms);
      if (typeof MutationObserver === "function" && document.body) {
        observer = new MutationObserver(finish);
        observer.observe(document.body, {childList: true, subtree: true, characterData: true, attributes: true});
      }
    });
  }
  async function until(check, timeout, failure) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const notice = blockingNotice();
      if (notice) throw Error(notice);
      const value = check();
      if (value) return value;
      await pageChange(400);
    }
    throw Error(failure);
  }
  function clearOwnDraft(prompt) {
    // O ChatGPT restaura o texto nao enviado de uma tarefa anterior; so apaga se for o nosso prompt.
    const input = editor();
    if (!input || !prompt || normalized(draftOf(input)) !== normalized(prompt)) return false;
    input.focus();
    if (input.tagName === "TEXTAREA") {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, "");
    } else {
      document.execCommand("selectAll", false);
      if (!document.execCommand("delete", false)) input.replaceChildren();
    }
    input.dispatchEvent(new InputEvent("input", {bubbles: true, composed: true, inputType: "deleteContentBackward"}));
    return !draftOf(input).trim();
  }
  function emptyConversation() {
    const input = editor();
    return !!input && !draftOf(input).trim() && !document.querySelector('[data-message-author-role="user"], [data-message-author-role="assistant"]') &&
      !Array.from(document.querySelectorAll('input[type="file"]')).some(node => node.files?.length);
  }
  function writePrompt(prompt) {
    const input = editor();
    if (!input) throw Error("O campo de mensagem desapareceu.");
    input.focus();
    if (input.tagName === "TEXTAREA") {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, prompt);
    } else if (input.isContentEditable) {
      // A edicao nativa atualiza o editor da pagina, inclusive ProseMirror.
      if (!document.execCommand("insertText", false, prompt)) input.replaceChildren(document.createTextNode(prompt));
    } else {
      throw Error("Campo de mensagem do ChatGPT nao reconhecido.");
    }
    input.dispatchEvent(new InputEvent("input", {bubbles: true, composed: true, inputType: "insertText", data: prompt}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
  }
  function attachmentPresent(name) {
    const scope = composer();
    if (!scope) return false;
    // O nome pode aparecer abreviado; antes do prompt, qualquer indicacao de PDF basta.
    const stem = name.replace(/\.pdf$/i, "").slice(0, 16);
    const texts = [textOf(scope), ...Array.from(scope.querySelectorAll('[title], [aria-label], [data-testid*="attachment"], [data-testid*="file"]'))
      .map(node => (node.getAttribute("title") || "") + " " + (node.getAttribute("aria-label") || "") + " " + textOf(node))];
    return texts.some(text => text.includes(name) || text.includes(stem) || /\.pdf\b|\bPDF\b/i.test(text));
  }
  function uploadBusy() {
    return !!composer()?.querySelector('[role="progressbar"], [aria-busy="true"], [data-state="uploading"], [data-testid="attachment-loading"]');
  }
  async function collectResponse() {
    await send("PROGRESS", "ChatGPT recebeu a mensagem; aguardando resposta completa.");
    let previous = "", stableSince = 0, lastProgress = 0;
    const start = Date.now();
    const text = await until(() => {
      const answer = finalAnswer();
      if (Date.now() - lastProgress >= 30000 || !lastProgress) {
        lastProgress = Date.now();
        // Diagnostico sem conteudo: somente sim/nao e contagens.
        const sim = value => value ? "sim" : "nao";
        send("PROGRESS", "Aguardando resposta do ChatGPT (" + Math.floor((Date.now() - start) / 1000) + "s) [texto " +
          sim(answer?.text) + ", pronta " + sim(answer?.finished) + ", gerando " + sim(generating()) +
          ", botoes " + actionButtons().length + ", blocos " + assistantMessages().length + "]").catch(() => {});
      }
      // Pausa na geracao nao e fim: exige controles finais, nada gerando e texto estavel.
      // Sem barra reconhecida, so aceita resposta numerica (1 a 9) parada por 3 s.
      const numerica = !!answer?.text && /^[1-9](?:\s*[,;]?\s*[1-9])*\.?$/.test(answer.text.trim()) && !generating();
      if (!answer?.finished && !numerica) { previous = ""; stableSince = 0; return false; }
      if (answer.text !== previous) { previous = answer.text; stableSince = Date.now(); return false; }
      return Date.now() - stableSince >= (answer.finished ? 1500 : 3000) ? answer.text : false;
    }, 10 * 60 * 1000, "O ChatGPT nao confirmou uma resposta completa em dez minutos. O PDF foi preservado.");
    // O servidor torna a entrega idempotente; conserva o resultado ate salvar.
    for (;;) {
      try {
        const response = await send("RESULT", "Resposta completa recebida do ChatGPT.", {text});
        if (response?.ok) break;
        if (response?.code === "NOT_ACTIVE" || response?.code === "WRONG_PROVIDER") throw Error("A tarefa mudou; resposta mantida nesta aba.");
      } catch (error) {
        if (/tarefa mudou/.test(error.message)) throw error;
      }
      await sleep(2000);
    }
  }
  async function resumeResponse() {
    try { await collectResponse(); }
    catch (error) { try { await send("ERROR", error.message || "Falha ao retomar a resposta."); } catch {} }
    finally { running = false; }
  }
  async function process(job) {
    let submitted = false;
    try {
      await until(composer, 45000, "Formulario do ChatGPT indisponivel. Confira o login na aba criada pela extensao.");
      // Da tempo ao ChatGPT de restaurar um rascunho antes de conferir a conversa.
      await sleep(600);
      clearOwnDraft(job.prompt);
      if (!emptyConversation()) throw Error("A conversa possui mensagens ou um rascunho. Nada foi substituido.");
      await until(fileInput, 15000, "Campo de anexo de PDF nao encontrado no ChatGPT. A interface pode ter mudado.");
      await send("PROGRESS", "Anexando PDF ao ChatGPT.");
      const file = new File([Uint8Array.from(atob(job.data), character => character.charCodeAt(0))], job.name, {type: "application/pdf"});
      job.data = "";
      let attempts = 0, lastAttempt = 0;
      const attach = () => {
        // Se o anexo nao aparecer, tenta de novo (ate 3 vezes), passando pelos campos disponiveis.
        const inputs = fileInputs();
        const input = inputs[attempts % Math.max(1, inputs.length)];
        attempts++;
        lastAttempt = Date.now();
        if (!input) return;
        const transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
        input.dispatchEvent(new Event("input", {bubbles: true}));
        input.dispatchEvent(new Event("change", {bubbles: true}));
      };
      attach();
      await until(() => {
        if (attachmentPresent(job.name)) return true;
        if (attempts < 3 && Date.now() - lastAttempt >= 8000) {
          send("PROGRESS", "Anexo nao confirmado; tentando de novo (" + (attempts + 1) + "/3).").catch(() => {});
          attach();
        }
        return false;
      }, 60000, "O ChatGPT nao confirmou o PDF anexado.");
      writePrompt(job.prompt);
      let readySince = 0;
      await until(() => {
        if (draftOf(editor()) === job.prompt && !uploadBusy() && !generating() && !disabled(sendButton())) {
          if (!readySince) readySince = Date.now();
          return Date.now() - readySince >= 600;
        }
        readySince = 0;
        return false;
      }, 120000, "O PDF ou o campo de mensagem nao terminou de carregar. Nada foi enviado novamente.");
      const button = sendButton();
      if (disabled(button) || !button.isConnected || generating()) throw Error("O botao Enviar mudou; envio interrompido.");
      await send("PROGRESS", "PDF anexado; mensagem enviada ao ChatGPT.");
      button.click();
      submitted = true;
      await until(() => messageSent(job.prompt, job.name), 60000,
        "O ChatGPT nao confirmou a mensagem. Confira a aba antes de repetir.");
      await collectResponse();
    } catch (error) {
      try { await send("ERROR", (error.message || "Falha no ChatGPT.") + (submitted ? " A mensagem pode ja ter sido enviada." : "")); } catch {}
    } finally { running = false; }
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    // O ID autentica a propria extensao; o Chrome pode omitir ou variar a URL do worker.
    if (sender.id !== chrome.runtime.id || sender.tab || message?.servidor !== APP) return;
    if (message.code === "PING") {
      const ready = !!composer();
      respond(reply(ready, ready ? "READY" : "WAIT_EDITOR", ready ? "Campo de mensagem pronto." :
        "A pagina abriu, mas o campo de mensagem nao esta disponivel. Confira o login na aba do ChatGPT.", {provider: PROVIDER}));
      return;
    }
    if (message.code === "RESUME") {
      if (location.origin !== "https://chatgpt.com" || message.provider !== PROVIDER ||
          typeof message.id !== "string" || typeof message.prompt !== "string" || !message.prompt.trim()) {
        respond(reply(false, "INVALID_JOB", "Retomada invalida.")); return;
      }
      if (running) { respond(reply(current?.id === message.id, "BUSY", "A coleta da resposta ja esta ativa.")); return; }
      if (!sentPrompt(message.prompt) && !/^\/c\//.test(location.pathname || "")) {
        respond(reply(false, "WAIT_MESSAGE", "Aguardando confirmacao da mensagem enviada; PDF nao reenviado.")); return;
      }
      current = message;
      running = true;
      respond(reply(true, "RESUMED", "Coleta retomada na aba da tarefa, sem reenviar PDF."));
      void resumeResponse();
      return;
    }
    if (message.code !== "PROCESS") return;
    if (running) { respond(reply(false, "BUSY", "Esta aba ja esta processando um PDF.")); return; }
    if (location.origin !== "https://chatgpt.com" || message.provider !== PROVIDER || typeof message.id !== "string" ||
        typeof message.data !== "string" || !message.data || message.data.length > 29 * 1024 * 1024 ||
        typeof message.name !== "string" || !/\.pdf$/i.test(message.name) || typeof message.prompt !== "string" || !message.prompt.trim()) {
      respond(reply(false, "INVALID_JOB", "Tarefa invalida."));
      return;
    }
    clearOwnDraft(message.prompt);
    if (!emptyConversation()) { respond(reply(false, "NOT_EMPTY", "Use somente a conversa nova criada para esta tarefa.")); return; }
    running = true;
    current = message;
    respond(reply(true, "ACCEPTED", "PDF recebido pela extensao."));
    void process(message);
  });
})();
'''

EXT_MANIFEST = r'''{
  "manifest_version": 3,
  "name": "PDF → ChatGPT Web",
  "version": "5.0.6",
  "minimum_chrome_version": "120",
  "description": "Anexa o PDF ao ChatGPT Web, envia e salva a resposta no programa local. Sem API.",
  "permissions": [
    "storage",
    "alarms",
    "scripting"
  ],
  "host_permissions": [
    "https://chatgpt.com/*",
    "http://127.0.0.1/*"
  ],
  "background": {
    "service_worker": "background.js"
  },
  "action": {
    "default_title": "PDF → ChatGPT Web"
  },
  "content_scripts": [
    {
      "matches": [
        "https://chatgpt.com/*"
      ],
      "js": [
        "chatgpt.js"
      ],
      "run_at": "document_idle"
    }
  ]
}
'''

EXTENSION_FILES = {"background.js": EXT_BACKGROUND, "chatgpt.js": EXT_CHATGPT, "manifest.json": EXT_MANIFEST}
EXT_VERSION = json.loads(EXT_MANIFEST)["version"]


def envelope(ok, code, message, **extra):
    return dict(servidor="separador", ok=ok, code=code, message=message, provedor=PROVIDER, **extra)


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


def token():
    safe_directory(CONFIG_PATH.parent)
    if CONFIG_PATH.is_symlink():
        raise ValueError("Configuracao local nao pode ser um link.")
    if CONFIG_PATH.exists():
        match = re.fullmatch(r'globalThis\.VIGIA_TOKEN="([a-f0-9]{64})";\s*',
                             CONFIG_PATH.read_text(encoding="utf-8"))
        if not match:
            raise ValueError("Configuracao local invalida; chave preservada.")
        return match.group(1)
    value = secrets.token_hex(32)
    with CONFIG_PATH.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write('globalThis.VIGIA_TOKEN="' + value + '";\n')
    return value


def materializar(base):
    destination = Path(base) / "pdf" / "extensao_deepseek"
    safe_directory(destination)
    for name, source in EXTENSION_FILES.items():
        target = destination / name
        if target.is_symlink():
            raise ValueError("Arquivo da extensao nao pode ser um link.")
        content = source.encode("utf-8")
        if not target.exists() or target.read_bytes() != content:
            atomic_bytes(target, content)
    # Restos de versoes antigas da extensao.
    for name in ("deepseek.js",):
        (destination / name).unlink(missing_ok=True)


def pdf_key(path):
    info = path.stat()
    return json.dumps([path.name, info.st_size, info.st_mtime_ns // 1000000],
                      ensure_ascii=False, separators=(",", ":"))


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
        self.last_contact = 0
        self.extension_version = ""
        self.state = self._load()
        self.persisted = copy.deepcopy(self.state)
        self.save()

    def _load(self):
        if not STATE_PATH.exists() and LEGACY_STATE_PATH.exists():
            with STATE_PATH.open("xb") as target:
                target.write(LEGACY_STATE_PATH.read_bytes())
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
            if job.get("status") == "aguardando" and job.get("provider", "deepseek") != PROVIDER:
                job.update(status="erro", error="Envio anterior ao DeepSeek; preservado sem reenviar.", finished=time.time())
            if job.get("status") == "enviando":
                job.update(status="erro", error="Envio anterior incerto; nao reenviado.")
            # Captura de uma execucao anterior: o robo ja nao espera por ela.
            if job.get("status") in ("fila", "preparando") and job.get("captura"):
                job.update(status="erro", error="Programa reiniciado antes do envio.", finished=time.time())
            # Enviada antes de reiniciar: nao bloqueia a fila; uma resposta tardia ainda vira TXT.
            if job.get("status") == "aguardando" and job.get("captura"):
                job.update(status="erro", error="Programa reiniciado antes da resposta.", finished=time.time())
            if job.get("status") in ("fila", "preparando"):
                job.update(status="fila", provider=PROVIDER)
                job.pop("tabId", None)
        state.update(envelope(True, "STATE", "Registro local do fluxo."), version=5)
        return state

    def save(self):
        with LOCK:
            try:
                write_json(STATE_PATH, self.state)
            except Exception:
                self.state = copy.deepcopy(self.persisted)
                raise
            self.persisted = copy.deepcopy(self.state)

    def active(self):
        return next((job for job in self.state["jobs"] if job.get("status") in
                     ("fila", "preparando", "aguardando")), None)

    def job(self, identifier):
        if not isinstance(identifier, str):
            raise ValueError("Identificador de tarefa invalido.")
        for job in self.state["jobs"]:
            if job.get("id") == identifier:
                return job
        raise ValueError("Tarefa nao encontrada.")

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
            job = dict(id=secrets.token_hex(16), key=pdf_key(path), name=path.name,
                       status="fila", created=time.time(), batch=secrets.token_hex(8),
                       captura=capture, progress="", error="", provider=PROVIDER)
            self.state["jobs"].append(job)
            self.save()
            NOVA_TAREFA.notify_all()
            return job

    def proximo(self, espera=0.0):
        """Devolve a tarefa ativa; sem tarefa, espera ate `espera` segundos (long-poll)."""
        with LOCK:
            deadline = time.monotonic() + max(0.0, min(float(espera), MAX_ESPERA))
            job = self.active()
            while job is None and time.monotonic() < deadline:
                NOVA_TAREFA.wait(deadline - time.monotonic())
                job = self.active()
            return envelope(True, "OK", "Proxima tarefa.", job=dict(job) if job else None,
                            extensao_versao=EXT_VERSION)

    def payload(self, job):
        with LOCK:
            path = safe_pdf(job["name"])
            info = path.stat()
            old_key = "%s:%s:%s" % (path.name, info.st_size, info.st_mtime_ns // 1000000)
            if job.get("key") not in (pdf_key(path), old_key):
                raise ValueError("O PDF mudou depois de entrar na fila.")
            if info.st_size > MAX_PAYLOAD:
                raise ValueError("PDF acima de 20 MB.")
            with path.open("rb") as stream:
                data = stream.read(MAX_PAYLOAD + 1)
            if len(data) > MAX_PAYLOAD or not data.startswith(b"%PDF-"):
                raise ValueError("PDF invalido ou acima de 20 MB.")
            job["sha256"] = hashlib.sha256(data).hexdigest()
            self.save()
            return dict(name=path.name, data=base64.b64encode(data).decode("ascii"))

    def result(self, job, text):
        with LOCK:
            if job.get("status") == "concluido":
                return job["output"]
            late = job.get("status") == "erro" and job.get("sentAt")
            if job.get("status") != "aguardando" and not late:
                raise ValueError("A tarefa nao esta aguardando resposta.")
            if (not isinstance(text, str) or not text.strip() or
                    len(text.encode("utf-8")) > MAX_RESULT):
                raise ValueError("Resposta vazia ou acima de 5 MB.")
            safe_directory(RESP_DIR)
            content = text.strip() + "\n"
            stem = Path(job["name"]).stem[:160]
            for number in range(10000):
                name = stem + ("" if number == 0 else "_chatgpt_" + str(number)) + ".txt"
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
            log("Resposta do ChatGPT em %.1fs, salva em pdf/respostas/%s"
                % (job["finished"] - float(job.get("created") or job["finished"]), name))
            # Uma falha na limpeza nunca impede uma resposta ja persistida.
            try:
                self.cleanup(safe_pdf(job["name"]), target)
            except OSError:
                logger.exception("Resposta salva; limpeza adiada.")
            if not late and ROBOT is not None and job.get("captura"):
                job["entrega"] = ROBOT.deliver(content, job["captura"])
                if job["entrega"] != "enviado":
                    log("Resposta nao executada (%s). Digite os numeros ou p." % job["entrega"])
            return job["output"]

    def falha(self, job, message):
        with LOCK:
            if job["status"] == "concluido":
                return
            job.update(status="erro", error=str(message)[:500], finished=time.time())
            self.save()
        log("Falha no ChatGPT: " + job["error"])
        if ROBOT is not None and job.get("captura"):
            ROBOT.falhou(job["captura"])

    def cleanup(self, keep_pdf, keep_txt):
        safe_directory(PDF_DIR)
        safe_directory(RESP_DIR)
        protected = {keep_pdf.resolve(), keep_txt.resolve()}
        protected.update(safe_pdf(job["name"]).resolve() for job in self.state["jobs"]
                         if job.get("status") in ("fila", "preparando", "aguardando"))
        for directory, pattern in ((PDF_DIR, "*.pdf"), (RESP_DIR, "*.txt")):
            for path in directory.glob(pattern):
                if (path.is_symlink() or not path.is_file() or
                        path.resolve().parent != directory.resolve() or path.resolve() in protected):
                    continue
                try:
                    path.unlink()
                except OSError:
                    logger.warning("Arquivo antigo ocupado; limpeza adiada.")

    def reap(self):
        expired = []
        with LOCK:
            for job in self.state["jobs"]:
                if job.get("status") == "aguardando" and time.time() - float(job.get("sentAt") or 0) > 900:
                    expired.append(job)
        for job in expired:
            self.falha(job, "Resposta nao confirmada em 15 minutos; nao reenviado automaticamente.")


ROTAS_EXTENSAO = ("/api/vigia/opened", "/api/vigia/payload", "/api/vigia/dispatch",
                  "/api/vigia/result", "/api/vigia/progress", "/api/vigia/error")


def executar(engine, route, data):
    """Rotas usadas pela extensao, sempre conferindo tarefa, estado e aba."""
    with LOCK:
        if route not in ROTAS_EXTENSAO:
            raise ValueError("Rota da extensao invalida.")
        job = engine.job(data.get("id"))
        if job.get("provider") != PROVIDER:
            raise ValueError("Tarefa de outro provedor; registro preservado.")
        if route == "/api/vigia/opened":
            if job["status"] not in ("fila", "preparando") or type(data.get("tabId")) is not int:
                raise ValueError("Abertura de tarefa invalida.")
            job.update(status="preparando", tabId=data["tabId"])
            engine.save()
            log("ChatGPT aberto no Chrome.")
            return envelope(True, "OK", "Aba registrada.")
        if route == "/api/vigia/payload":
            if job["status"] != "preparando":
                raise ValueError("A tarefa nao esta em preparacao.")
            return envelope(True, "OK", "PDF preparado.", **engine.payload(job))
        if route == "/api/vigia/dispatch":
            if job["status"] != "preparando":
                raise ValueError("A tarefa nao esta em preparacao.")
            job.update(status="aguardando", sentAt=time.time(), progress="Preparando envio ao ChatGPT.")
            engine.save()
            return envelope(True, "OK", "Envio registrado.")
        if route == "/api/vigia/result":
            if type(data.get("tabId")) is not int or data["tabId"] != job.get("tabId"):
                raise ValueError("A resposta pertence a outra aba.")
            if job["status"] != "concluido":
                engine.result(job, data.get("text", ""))
            return envelope(True, "SAVED", "Resposta salva.", output="respostas/" + Path(job["output"]).name)
        if route == "/api/vigia/progress":
            if job["status"] not in ("preparando", "aguardando"):
                raise ValueError("A tarefa nao esta em preparacao ou aguardando resposta.")
            message = str(data.get("message", ""))[:300]
            if message and message != job.get("progress"):
                log("ChatGPT: " + message)
            job["progress"] = message
            engine.save()
            return envelope(True, "OK", "Andamento registrado.")
    # /api/vigia/error: fora do LOCK para o robo poder reagir.
    engine.falha(job, data.get("message", data.get("error", "")))
    return envelope(True, "OK", "Erro registrado.")


class Handler(BaseHTTPRequestHandler):
    """Somente /api/status e as rotas da extensao. Nao serve arquivos."""

    def setup(self):
        super().setup()
        self.connection.settimeout(MAX_ESPERA + 15)

    def log_message(self, format, *args):
        logger.debug("HTTP %s", self.command)

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def status(self, status, code, message, **extra):
        self.respond(status, envelope(status == 200, code, message, **extra))

    def send_error(self, code, message=None, explain=None):
        self.status(code, "HTTP_ERROR", message or "Requisicao nao permitida.")

    def local_host(self):
        return self.headers.get("Host", "") == "127.0.0.1:%s" % self.server.server_port

    def authorized_extension(self):
        if self.headers.get("X-Vigia-Provider") != PROVIDER:
            return False
        origin = self.headers.get("Origin", "")
        if not self.local_host() or (origin and not re.fullmatch(r"chrome-extension://[a-p]{32}", origin)):
            return False
        try:
            return bool(SECRET) and hmac.compare_digest(self.headers.get("X-Vigia-Token", ""), SECRET)
        except TypeError:
            return False

    def extension_contact(self):
        version = self.headers.get("X-Vigia-Version", "")
        with LOCK:
            first = ENGINE.last_contact == 0
            ENGINE.last_contact = time.time()
            if re.fullmatch(r"[0-9.]{1,40}", version):
                ENGINE.extension_version = version
        if first:
            log("Extensao do Chrome conectada (versao %s)." % (version or "?"))

    def read_json(self, limit):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Envie o tamanho do corpo da requisicao.")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= limit:
            raise ValueError("Tamanho da requisicao invalido.")
        content = self.rfile.read(length)
        if len(content) != length:
            raise ValueError("Requisicao incompleta.")
        value = json.loads(content.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("O corpo JSON deve ser um objeto.")
        return value

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        try:
            if not self.local_host():
                self.status(403, "FAILED", "Endereco local invalido.")
            elif url.path == "/api/status":
                self.status(200, "pronto", "Programa pronto.", versao=VERSAO)
            elif url.path == "/api/vigia/next":
                if not self.authorized_extension():
                    self.status(403, "FAILED", "Acesso da extensao nao autorizado.")
                    return
                self.extension_contact()
                espera = urllib.parse.parse_qs(url.query).get("espera", ["0"])[0]
                self.respond(200, ENGINE.proximo(float(espera) if re.fullmatch(r"\d{1,2}", espera) else 0))
            else:
                self.status(404, "NOT_FOUND", "Rota desconhecida.")
        except (ValueError, OSError) as error:
            self.status(400, "FAILED", str(error))
        except Exception:
            logger.exception("Falha na requisicao GET.")
            self.status(500, "FAILED", "Nao foi possivel atender a requisicao.")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if not path.startswith("/api/vigia/"):
                self.status(404, "NOT_FOUND", "Rota desconhecida.")
            elif not self.authorized_extension():
                self.status(403, "FAILED", "Acesso nao autorizado.")
            else:
                self.extension_contact()
                self.respond(200, executar(ENGINE, path, self.read_json(30 * 1024 * 1024)))
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.status(400, "FAILED", str(error))
        except Exception:
            logger.exception("Falha na requisicao POST.")
            self.status(500, "FAILED", "Falha no programa; dados anteriores preservados.")


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def service_actions(self):
        now = time.monotonic()
        if now >= getattr(self, "next_maintenance", 0):
            self.next_maintenance = now + 20
            try:
                ENGINE.reap()
            except Exception:
                logger.exception("Falha na verificacao da fila; dados preservados.")


class Robo:
    """Captura, gera o PDF e executa a resposta, tudo no mesmo processo."""

    MAX_TENTATIVAS = 3
    MAX_REPETICOES = 2

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
            self.message = "Captura pronta; aguardando ChatGPT."
            return self.version

    def atual(self, version):
        with LOCK:
            return bool(self.waiting and version and version == self.version)

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
        """Falha no ChatGPT: tira outra captura sozinho, no maximo duas vezes seguidas."""
        with LOCK:
            if not self.atual(version):
                return
            if self.repeticoes >= self.MAX_REPETICOES:
                log("Falhou de novo. Digite p para nova captura ou os numeros (ex.: 1,5,9).")
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
            if png is None:
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
            ENGINE.receber_pdf(pdf, version)
            log("Captura + PDF em %.1fs; enviando ao ChatGPT." % (time.monotonic() - inicio))
            return

    def aviso(self, message):
        with LOCK:
            self.waiting = True
            self.message = message
        log(message)

    def run(self):
        browser = None
        try:
            browser = BrowserRobo(self.stop_event)
            browser.open()
            try:
                self.capturar(browser, None)
            except ValueError as error:
                self.aviso(str(error))
            while not self.stop_event.is_set():
                try:
                    version, answer = self.inbox.get(timeout=0.5)
                except queue.Empty:
                    continue
                if answer == "s":
                    break
                with LOCK:
                    if version != self.version:
                        continue
                try:
                    anterior = None
                    if answer != "p":
                        anterior = browser.answer(answer)
                    self.capturar(browser, anterior)
                except ValueError as error:
                    self.aviso(str(error))
        except Exception as error:
            with LOCK:
                self.message = "Robo interrompido: " + str(error).splitlines()[0]
            log(self.message)
            logger.debug("Falha no robo.", exc_info=True)
        finally:
            with LOCK:
                self.waiting = False
            if browser is not None:
                browser.close_driver()


class BrowserRobo:
    POSITIONS = {1: (0.176, 0.317), 2: (0.500, 0.317), 3: (0.824, 0.317),
                 4: (0.176, 0.533), 5: (0.500, 0.533), 6: (0.824, 0.533),
                 7: (0.176, 0.749), 8: (0.500, 0.749), 9: (0.824, 0.749)}
    BUTTON = (0.872, 0.953)

    def __init__(self, stop_event):
        # Selenium ja era utilizado pelo robo; o servidor continua stdlib.
        try:
            from selenium import webdriver
            from selenium.webdriver.common.action_chains import ActionChains
        except ImportError as error:
            raise RuntimeError("Selenium ausente. Execute INSTALAR.bat.") from error
        options = webdriver.ChromeOptions()
        # Mesmas opcoes do robo original (sem sinais de automacao).
        options.add_argument("--start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("detach", True)
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(60)
        self.driver.set_script_timeout(30)
        self.actions = ActionChains
        self.stop_event = stop_event
        self.last = None

    def open(self):
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        self.driver.get("https://cav.receita.fazenda.gov.br/autenticacao/login")
        try:
            button = WebDriverWait(self.driver, 30).until(EC.element_to_be_clickable(
                ("xpath", "//input[@type='image' and (contains(@src,'gov-br') or @alt='Acesso Gov BR')]")))
            button.click()
        except Exception:
            log("Aguardando a imagem de verificacao na pagina aberta.")

    def frame(self):
        self.driver.switch_to.default_content()
        candidates = []
        for element in self.driver.find_elements("css selector", "iframe"):
            try:
                size = element.size
                if element.is_displayed() and size["height"] > size["width"] > 200:
                    candidates.append((size["width"] * size["height"], element))
            except Exception:
                continue
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    # As imagens do desafio aparecem aos poucos: esperar menos que isso gera grade incompleta.
    ESPERA_INICIAL = 3.0
    ESPERA_APOS_RESPOSTA = 2.0

    def capture(self, anterior=None, espera=None):
        """Espera a imagem carregar e parar de mudar, e a captura."""
        inicio = time.monotonic()
        if espera is None:
            espera = self.ESPERA_APOS_RESPOSTA if anterior else self.ESPERA_INICIAL
        if self.stop_event.wait(espera):
            return None
        last = None
        while not self.stop_event.is_set() and time.monotonic() - inicio < 120:
            frame = self.frame()
            png = None
            if frame is not None:
                try:
                    png = frame.screenshot_as_png
                except Exception:
                    png = None
            if png:
                decorrido = time.monotonic() - inicio
                nova = png != anterior or decorrido > 10
                if nova and (png == last or decorrido > 6):
                    self.last = png
                    return png
            last = png
            self.stop_event.wait(0.5)
        if not self.stop_event.is_set():
            raise ValueError("A imagem nao apareceu em dois minutos. Digite p para tentar outra captura.")
        return None

    def gerar_pdf(self, png):
        """Gera o PDF numerado no navegador do robo, sem abrir pagina."""
        self.driver.switch_to.default_content()
        try:
            value = self.driver.execute_async_script(GERAR_PDF_JS, base64.b64encode(png).decode("ascii"))
        except Exception as error:
            raise ValueError("Falha ao gerar o PDF: " + (str(error).strip().splitlines() or ["erro"])[0]) from error
        if not isinstance(value, dict) or not value.get("ok"):
            raise ValueError((value or {}).get("message") or "Falha ao gerar o PDF.")
        if value.get("completada"):
            log("Linhas da grade montadas pelas posicoes fixas do 3x3.")
        return base64.b64decode(value["pdf"])

    def click(self, frame, point):
        """Igual ao robo original: rola ate o centro, espera parar e clica a partir do centro do quadro."""
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", frame)
        time.sleep(0.2)
        width, height = frame.size["width"], frame.size["height"]
        dx, dy = int(width * point[0] - width / 2), int(height * point[1] - height / 2)
        self.actions(self.driver).move_to_element_with_offset(frame, dx, dy).click().perform()

    def answer(self, text):
        """Clica nas posicoes e confirma. Devolve a captura anterior para detectar a troca."""
        values = parse_numbers(text)
        if text != "v" and not values:
            raise ValueError("Use numeros de 1 a 9, p, v ou s.")
        frame = self.frame()
        if frame is None:
            raise ValueError("A imagem nao esta na tela. Digite p para tentar outra captura.")
        for number in values:
            if self.stop_event.is_set():
                return self.last
            self.click(frame, self.POSITIONS[number])
            self.stop_event.wait(0.3)
        if not self.stop_event.wait(0.5):
            self.click(frame, self.BUTTON)
        return self.last

    def close_driver(self):
        try:
            self.driver.service.stop()
        except Exception:
            pass


def chrome_path():
    try:
        import winreg
    except ImportError:
        winreg = None
    if winreg is not None:
        key = r"Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, key) as handle:
                    candidate = Path(winreg.QueryValueEx(handle, "")[0])
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                pass
    for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        if os.environ.get(name):
            candidate = Path(os.environ[name]) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def acordar_extensao(segundos=8):
    """Se a extensao nao falar com o programa, abre o Chrome do usuario (sem pagina local)."""
    if threading.current_thread() is threading.main_thread():
        threading.Thread(target=acordar_extensao, args=(segundos,), daemon=True).start()
        return
    time.sleep(segundos)
    if ENGINE.last_contact:
        return
    log("Extensao ainda nao conectou; abrindo o Chrome com o perfil %s." % PROFILE_DIRECTORY)
    executable = chrome_path()
    try:
        if executable and os.name == "nt":
            os.startfile(executable, "open", '--profile-directory="%s"' % PROFILE_DIRECTORY)
        else:
            webbrowser.open("about:blank")
    except (OSError, TypeError):
        log("Abra o Chrome com a extensao PDF -> ChatGPT Web ativa.")


class InstanceLock:
    def __enter__(self):
        directory = APP_DIR / "tmp"
        safe_directory(directory)
        self.stream = (directory / "servidor.lock").open("a+b")
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


def bind_server(start_port):
    error = None
    for port in range(start_port, start_port + 20):
        try:
            return LocalServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            error = exc
    raise error


def main(argv=None):
    global SECRET, ENGINE, ROBOT
    parser = argparse.ArgumentParser(description="Captura -> PDF -> ChatGPT -> robo, tudo no terminal.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sem-robo", action="store_true", help="somente o servidor da extensao")
    parser.add_argument("--preparar-extensao", action="store_true", help="gera os arquivos da extensao e encerra")
    # Atalhos antigos continuam funcionando.
    parser.add_argument("--abrir", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--robo", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    if not 1 <= args.port <= 65515:
        parser.error("A porta deve ficar entre 1 e 65515.")
    try:
        with InstanceLock():
            SECRET = token()
            materializar(APP_DIR)
            if args.preparar_extensao:
                print("Extensao preparada em pdf/extensao_deepseek.")
                return 0
            ENGINE = Engine()
            ROBOT = Robo()
            httpd = bind_server(args.port)
            try:
                log("Programa pronto (porta %s). Comandos: numeros (1,5,9) | p nova captura | v verificar | s parar | Ctrl+C sair."
                    % httpd.server_port)
                acordar_extensao()
                if not args.sem_robo:
                    ROBOT.start()
                signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
                httpd.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                pass
            finally:
                ROBOT.stop()
                httpd.server_close()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
