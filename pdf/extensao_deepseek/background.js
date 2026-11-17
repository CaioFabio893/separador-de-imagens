"use strict";
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
