"use strict";
importScripts("config_local.js");
const PORTS = Array.from({length: 20}, (_, index) => 8765 + index);
const PROMPT = "Responda ao PDF anexado seguindo as instruções contidas nele. Preserve a numeração solicitada. Não faça pesquisas na web.";
const result = (ok, code, message) => ({servidor: "separador", ok, code, message});
let busy = false;
let base = "";

function validBase(value) {
  return typeof value === "string" && /^http:\/\/127\.0\.0\.1:\d{4,5}$/.test(value);
}

async function probe(candidate) {
  const response = await fetch(candidate + "/api/status", {signal: AbortSignal.timeout(1500)});
  const value = await response.json();
  return response.ok && value.ok && value.servidor === "separador";
}

async function findBase() {
  if (validBase(base)) return base;
  const first = "http://127.0.0.1:" + PORTS[0];
  try {
    if (await probe(first)) {
      base = first;
      return base;
    }
  } catch {}
  const attempts = await Promise.allSettled(PORTS.slice(1).map(async port => {
    const candidate = "http://127.0.0.1:" + port;
    if (await probe(candidate)) return candidate;
    throw Error("nao e o separador");
  }));
  const found = attempts.find(item => item.status === "fulfilled");
  if (!found) throw Error("Abra o Separador de Imagens pelo atalho para ativar o fluxo.");
  base = found.value;
  return base;
}

async function api(path, data) {
  for (let attempt = 0; ; attempt++) {
    const root = await findBase();
    let response;
    try {
      response = await fetch(root + path, {
        method: data ? "POST" : "GET",
        headers: {"X-Vigia-Token": VIGIA_TOKEN, ...(data ? {"Content-Type": "application/json"} : {})},
        body: data ? JSON.stringify(data) : undefined,
        signal: AbortSignal.timeout(15000)
      });
    } catch (error) {
      base = "";
      if (attempt >= 1) throw error;
      continue;
    }
    const value = await response.json();
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

async function openTab() {
  const url = "https://chat.deepseek.com/";
  const windows = await chrome.windows.getAll({windowTypes: ["normal"]});
  for (const window of windows) {
    try { return await chrome.tabs.create({windowId: window.id, url, active: false}); } catch {}
  }
  // Nunca cria uma janela do Chrome: usa apenas uma que o usuario ja abriu.
  return null;
}

async function tick() {
  if (busy) return;
  busy = true;
  let job, fatal = false;
  try {
    job = (await api("/api/vigia/next")).job;
    if (!job) { await badge("", "Fluxo PDF: aguardando um PDF novo."); return; }
    if (job.status === "aguardando") {
      if (!(await tabAlive(job.tabId))) {
        fatal = true;
        throw Error("A aba do DeepSeek foi fechada antes da resposta. O PDF pode já ter sido recebido.");
      }
      await badge("…", "DeepSeek processando o PDF.");
      return;
    }
    // Nada foi enviado ainda: se a aba sumiu, basta abrir outra.
    if (job.status === "preparando" && !(await tabAlive(job.tabId))) job = {...job, status: "fila"};
    if (job.status === "fila") {
      const tab = await openTab();
      if (!tab?.id) { await badge("…", "PDF na fila: abra uma janela do Chrome para enviar."); return; }
      await chrome.tabs.update(tab.id, {autoDiscardable: false});
      await api("/api/vigia/opened", {id: job.id, tabId: tab.id});
      job = {...job, status: "preparando", tabId: tab.id};
    }
    let ready;
    try {
      ready = await chrome.tabs.sendMessage(job.tabId, {...result(true, "PING", "Verificar DeepSeek.")});
    } catch {
      await badge("…", "Aguardando o DeepSeek. Entre na conta se solicitado.");
      return;
    }
    if (!ready?.ok) {
      await badge("!", "Faça login na aba do DeepSeek; o fluxo continuará sozinho.");
      return;
    }
    fatal = true;
    const payload = await api("/api/vigia/payload", {id: job.id});
    await api("/api/vigia/dispatch", {id: job.id});
    const accepted = await chrome.tabs.sendMessage(job.tabId, {
      ...result(true, "PROCESS", "Processar PDF."), id: job.id, name: payload.name,
      data: payload.data, prompt: PROMPT
    });
    if (!accepted?.ok) throw Error(accepted?.message || "O envio não foi confirmado.");
    await badge("…", "DeepSeek processando o PDF.");
  } catch (error) {
    // Problemas passageiros antes do envio (sem janela, pagina carregando) so tentam depois.
    if (job && fatal) try { await api("/api/vigia/error", {id: job.id, message: error.message}); } catch {}
    await badge("!", "Fluxo: " + error.message);
  } finally {
    busy = false;
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || message?.servidor !== "separador" ||
      !sender.tab || !sender.url?.startsWith("https://chat.deepseek.com/")) return;
  (async () => {
    if (message.code === "RESULT") {
      // O servidor confere tarefa + aba e nunca grava a mesma resposta duas vezes.
      const saved = await api("/api/vigia/result",
        {id: message.id, tabId: sender.tab.id, text: message.text});
      respond(saved);
      await chrome.tabs.remove(sender.tab.id).catch(() => {});
      await tick();
      return;
    }
    const job = (await api("/api/vigia/next")).job;
    if (!job || job.id !== message.id || job.tabId !== sender.tab.id) {
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
      // Guarda o andamento no servidor para a pagina e o robo acompanharem.
      try { await api("/api/vigia/progress", {id: message.id, message: message.message}); } catch {}
      respond(result(true, "ACK", "Processamento em andamento."));
      return;
    }
  })().catch(error => respond(result(false, "FAILED", error.message)));
  return true;
});

// A pagina do aplicativo avisa quando um PDF acabou de ser gerado.
chrome.runtime.onMessageExternal.addListener((message, sender, respond) => {
  const origin = sender.origin || "";
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(origin)) return;
  // Recarrega a propria extensao quando a pagina local pede (apos atualizar).
  if (message?.type === "RECARREGAR") {
    respond({ok: true, versao: chrome.runtime.getManifest().version});
    setTimeout(() => chrome.runtime.reload(), 300);
    return;
  }
  if (message?.type !== "CHECK") return;
  if (validBase(message.base)) base = message.base;
  respond({ok: true});
  void tick();
});

async function initialize() {
  await chrome.alarms.clear("vigia-check");
  await chrome.alarms.create("fluxo-check", {periodInMinutes: 0.5});
  await tick();
}
chrome.runtime.onInstalled.addListener(() => void initialize());
chrome.runtime.onStartup.addListener(() => void initialize());
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "fluxo-check") void tick(); });
chrome.tabs.onUpdated.addListener((_id, change, tab) => {
  if (change.status === "complete" && tab.url?.startsWith("https://chat.deepseek.com/")) void tick();
});
chrome.action.onClicked.addListener(() => void tick());
void initialize();
