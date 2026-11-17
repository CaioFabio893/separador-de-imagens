"use strict";
// Testes sinteticos: sem navegador real, rede externa, imagens, PDFs ou conversas.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "servidor_separador.py"), "utf8");
const embedded = name => source.split(name + " = r'''", 2)[1].split("'''", 1)[0];
const settle = () => new Promise(resolve => setImmediate(resolve));

// ---------- Nucleo do PDF: grade, recortes, numeracao ----------
function nucleo(fixture) {
  const sandbox = {TextEncoder, Blob, Uint8Array, Uint8ClampedArray, Float32Array, Uint16Array, Map, console,
    document: {createElement: () => ({getContext: () => ({fillRect() {}, drawImage() {}, getImageData: () => ({data: fixture.rgba})})})}};
  vm.createContext(sandbox);
  vm.runInContext(embedded("NUCLEO_JS") + "\nglobalThis.test = {analyze, makePdf, headerBox};", sandbox);
  return sandbox.test;
}
function fixture(gap, background = 255) {
  const width = 300, height = 360;
  const gray = new Uint8Array(width * height).fill(background);
  for (let row = 0; row < 3; row++) for (let col = 0; col < 3; col++) {
    const left = 10 + col * (90 + gap), top = 65 + row * (80 + gap);
    for (let y = top; y < top + 80; y++) for (let x = left; x < left + 90; x++) gray[y * width + x] = 70 + row * 20 + col * 7;
  }
  const rgba = new Uint8ClampedArray(width * height * 4);
  gray.forEach((v, i) => rgba.set([v, v, v, 255], i * 4));
  return {width, height, rgba};
}
async function testNucleo() {
  for (const [gap, bg] of [[5, 255], [1, 255], [1, 238]]) {
    const data = fixture(gap, bg);
    // ImageBitmap (navegador do robo) usa width/height em vez de naturalWidth.
    const result = nucleo(data).analyze({width: data.width, height: data.height}, {whiteThr: 244, gapMin: 5});
    assert.equal(result.rows, 3);
    assert.equal(result.cols, 3);
    assert.deepEqual(Array.from(result.cells, cell => cell.n), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  }
  const empty = fixture(5);
  empty.rgba.fill(255);
  assert.ok(nucleo(empty).analyze({width: empty.width, height: empty.height}, {whiteThr: 244, gapMin: 5}).error);
  const pdf = await nucleo(fixture(5)).makePdf([{width: 2, height: 2, jpeg: new Uint8Array([255, 216, 255, 217])}]).text();
  assert.ok(pdf.startsWith("%PDF-1.4") && pdf.trimEnd().endsWith("%%EOF"));
  const xref = Number(pdf.match(/startxref\n(\d+)/)[1]);
  assert.equal(pdf.slice(xref, xref + 4), "xref");
  pdf.slice(xref).split("\n").slice(3, 8).forEach((entry, i) =>
    assert.ok(pdf.slice(Number(entry.slice(0, 10))).startsWith((i + 1) + " 0 obj")));
}

// ---------- Service worker da extensao ----------
function worker() {
  const events = {}, state = {job: null, ready: true, provider: "chatgpt_web", rejectResult: false, missingScript: false,
    injections: [], created: [], closed: [], sent: [], storage: {}, tabs: new Map([[99, {id: 99}]]), reloads: 0, waits: [], version: null};
  const event = name => ({addListener: listener => events[name] = listener});
  const response = (extra = {}) => ({servidor: "separador", ok: true, code: "OK", message: "teste", provedor: state.provider, ...extra});
  const sandbox = {console, AbortSignal, setTimeout, importScripts: name => assert.equal(name, "config_local.js"), VIGIA_TOKEN: "token-sintetico",
    fetch: async (url, options = {}) => {
      const parsed = new URL(url);
      assert.equal(parsed.hostname, "127.0.0.1");
      if (parsed.pathname === "/api/status") return {ok: true, json: async () => response()};
      assert.equal(options.headers["X-Vigia-Token"], "token-sintetico");
      assert.equal(options.headers["X-Vigia-Provider"], "chatgpt_web");
      const body = options.body ? JSON.parse(options.body) : {};
      let value;
      if (parsed.pathname === "/api/vigia/next") {
        state.waits.push(parsed.searchParams.get("espera"));
        value = response({job: state.job ? {...state.job} : null, ...(state.version ? {extensao_versao: state.version} : {})});
      } else if (parsed.pathname === "/api/vigia/opened") { state.job.status = "preparando"; state.job.tabId = body.tabId; value = response(); }
      else if (parsed.pathname === "/api/vigia/payload") value = response({name: "sintetico.pdf", data: "JVBERi0xLjQKJSVFT0Y="});
      else if (parsed.pathname === "/api/vigia/dispatch") { state.job.status = "aguardando"; value = response(); }
      else if (parsed.pathname === "/api/vigia/progress") { state.progress = body.message; value = response(); }
      else if (parsed.pathname === "/api/vigia/result") {
        if (state.rejectResult) value = response({ok: false, code: "FAILED", message: "falha sintetica ao salvar"});
        else { state.saved = {...body}; state.job = null; value = response({code: "SAVED"}); }
      } else if (parsed.pathname === "/api/vigia/error") { state.error = body.message; state.job = null; value = response(); }
      else throw Error("Rota inesperada: " + parsed.pathname);
      return {ok: value.ok, json: async () => value};
    },
    chrome: {
      runtime: {id: "extensao-teste", getManifest: () => ({version: "5.0.0"}), reload: () => state.reloads++,
        // O laco permanente fica parado no teste; cada passo e chamado por tick().
        getPlatformInfo: () => new Promise(() => {}),
        onMessage: event("message"), onInstalled: event("installed"), onStartup: event("startup")},
      scripting: {executeScript: async options => { state.injections.push(options.target.tabId); state.missingScript = false; }},
      storage: {local: {get: async key => key === null ? {...state.storage} : ({[key]: state.storage[key]}),
        set: async values => Object.assign(state.storage, values),
        remove: async keys => [].concat(keys).forEach(key => delete state.storage[key])}},
      action: {setBadgeText: async () => {}, setTitle: async () => {}, onClicked: event("click")},
      alarms: {create: async () => {}, onAlarm: event("alarm")},
      windows: {getAll: async () => [{id: 1, focused: true}], onCreated: event("window")},
      tabs: {get: async id => { if (!state.tabs.has(id)) throw Error("aba ausente"); return state.tabs.get(id); },
        create: async options => {
          assert.equal(options.url, "https://chatgpt.com/");
          assert.equal(options.active, true, "aba ativa evita lentidao em segundo plano");
          const tab = {id: state.created.length + 1, url: options.url};
          state.created.push({...options, ...tab}); state.tabs.set(tab.id, tab); return tab;
        },
        update: async () => {}, remove: async id => { state.closed.push(id); state.tabs.delete(id); },
        sendMessage: async (id, message) => {
          if (message.code === "PING") {
            if (state.missingScript) throw Error("No receiving end");
            return {ok: state.ready, provider: "chatgpt_web", message: state.ready ? "Pronto" : "Campo ainda indisponivel"};
          }
          if (message.code === "RESUME") { state.resumed = {id, message}; return {ok: true, code: "RESUMED", message: "Coleta retomada."}; }
          assert.equal(message.provider, "chatgpt_web");
          state.sent.push({id, message});
          return {ok: true};
        }, onUpdated: event("updated")}
    }};
  vm.createContext(sandbox);
  vm.runInContext(embedded("EXT_BACKGROUND"), sandbox);
  return {state, tick: espera => sandbox.tick(espera),
    message: (message, tabId) => new Promise(resolve => events.message({servidor: "separador", provider: "chatgpt_web", ...message},
      {id: "extensao-teste", url: "https://chatgpt.com/c/conversa-sintetica", tab: {id: tabId}}, resolve))};
}
async function testWorker() {
  let test = worker(); await settle();
  assert.equal(await test.tick(20), "idle");
  assert.equal(test.state.waits.at(-1), "20", "espera a proxima tarefa no servidor (sem atraso de alarme)");
  test.state.job = {id: "tarefa1", status: "fila", provider: "chatgpt_web"};
  test.state.ready = false;
  await test.tick(); await test.tick();
  assert.equal(test.state.created.length, 1, "aguarda login na mesma aba");
  assert.equal(test.state.sent.length, 0, "nao envia antes de o formulario estar pronto");
  assert.equal(test.state.progress, "Campo ainda indisponivel");
  test.state.ready = true;
  await test.tick(); await test.tick();
  assert.equal(test.state.sent.length, 1, "um unico envio por tarefa");
  assert.equal(test.state.job.status, "aguardando");
  assert.equal(test.state.resumed.id, 1, "retoma somente a coleta, sem reenviar PDF");
  assert.equal((await test.message({code: "RESULT", id: "tarefa1", text: "2,9"}, 99)).code, "NOT_ACTIVE");
  assert.equal(test.state.closed.length, 0, "nao fecha aba do usuario");
  test.state.rejectResult = true;
  assert.equal((await test.message({code: "RESULT", id: "tarefa1", text: "2,9"}, 1)).ok, false);
  assert.equal(test.state.closed.length, 0, "preserva a aba enquanto a resposta nao foi salva");
  test.state.rejectResult = false;
  assert.equal((await test.message({code: "RESULT", id: "tarefa1", text: "2,9"}, 1)).code, "SAVED");
  await settle();
  assert.deepEqual(test.state.closed, [1]);
  assert.equal(test.state.saved.text, "2,9");
  assert.equal(test.state.storage["chatgpt-task-tarefa1"], undefined);

  test = worker(); await settle();
  test.state.provider = "deepseek";
  test.state.job = {id: "antiga", status: "fila", provider: "deepseek"};
  await test.tick();
  assert.equal(test.state.created.length, 0, "servidor antigo nao dispara ChatGPT");

  test = worker(); await settle();
  test.state.job = {id: "perdida", status: "aguardando", provider: "chatgpt_web", tabId: 99};
  await test.tick();
  assert.ok(test.state.error, "nao reutiliza aba sem vinculo com a tarefa");
  assert.equal(test.state.created.length, 0, "nao reenvia mensagem ja enviada");

  test = worker(); await settle();
  test.state.job = {id: "reconectar", status: "fila", provider: "chatgpt_web"};
  test.state.missingScript = true;
  await test.tick();
  assert.deepEqual(test.state.injections, [1], "reconecta somente a aba criada pela tarefa");
  assert.equal(test.state.sent.length, 1);
  test.state.missingScript = true;
  await test.tick();
  assert.equal(test.state.resumed.message.code, "RESUME");
  assert.equal(test.state.sent.length, 1, "reconexao da coleta nao reenvia o PDF");

  test = worker(); await settle();
  test.state.job = {id: "login", status: "preparando", provider: "chatgpt_web", tabId: 1};
  test.state.storage["chatgpt-task-login"] = {tabId: 1};
  test.state.tabs.set(1, {id: 1, url: "https://auth.openai.com/"});
  test.state.missingScript = true;
  await test.tick();
  assert.deepEqual(test.state.injections, [], "nao injeta codigo fora do dominio autorizado");
  assert.ok(test.state.progress);

  test = worker(); await settle();
  test.state.version = "9.9.9";
  test.state.job = {id: "ativa", status: "fila", provider: "chatgpt_web"};
  test.state.ready = false;
  await test.tick();
  assert.equal(test.state.reloads, 0, "nao recarrega com tarefa ativa");
  test.state.job = null;
  await test.tick(); await test.tick();
  assert.equal(test.state.reloads, 1, "recarrega sozinha uma vez quando o programa foi atualizado");
}

// ---------- Script do ChatGPT: coleta da resposta ----------
async function scenario(kind) {
  let clock = 0, listener, writes = 0, finished;
  const done = new Promise(resolve => finished = resolve);
  const visible = {isConnected: true, hidden: false, getClientRects: () => [{}]};
  const control = {...visible};
  const block = {...visible, innerText: "4,6", closest: () => null, querySelector: () => null, contains: () => false};
  const turn = {querySelectorAll: selector => selector.includes("button") ? [control] : []};
  const message = {...visible, closest: selector => selector.includes("conversation-turn") ? turn : null, querySelector: () => null,
    querySelectorAll: selector => selector === ".markdown" ? (kind === "markdown" ? [block] : []) :
      selector.includes("message-content") ? (kind === "content" ? [block] : []) : [],
    cloneNode: () => ({textContent: "4,6", querySelectorAll: () => []})};
  const user = {...visible, innerText: "Prompt\n\n sintetico"};
  const document = {querySelectorAll: selector => {
    if (selector.includes('data-message-author-role="user"')) return [user];
    if (selector.includes('data-message-author-role="assistant"')) return [message];
    if (selector.includes("stop-button")) return clock < 12000 ? [control] : [];
    return [];
  }};
  const context = {document, location: {origin: "https://chatgpt.com"}, Date: {now: () => clock},
    setTimeout: (callback, ms) => { clock += ms; setImmediate(callback); return 1; }, clearTimeout: () => {},
    chrome: {runtime: {id: "sintetica", onMessage: {addListener: fn => listener = fn}, sendMessage: async data => {
      if (data.code === "ERROR") { finished({error: data.message}); return {ok: true}; }
      if (data.code === "RESULT") {
        assert.ok(clock >= 13500, "aguarda o fim da geracao e a estabilidade do texto");
        assert.equal(data.text, "4,6");
        writes++;
        if (writes === 1) return {ok: false, code: "FAILED"};
        finished({ok: true});
        return {ok: true, code: "SAVED"};
      }
      return {ok: true};
    }}}};
  vm.createContext(context);
  vm.runInContext(embedded("EXT_CHATGPT"), context);
  const resume = {servidor: "separador", code: "RESUME", provider: "chatgpt_web", id: "tarefa-sintetica", prompt: "Prompt sintetico"};
  let response;
  listener(resume, {id: "estranha"}, data => response = data);
  assert.equal(response, undefined, "recusa outra extensao");
  listener({...resume, prompt: "outra mensagem"}, {id: "sintetica"}, data => response = data);
  assert.equal(response.code, "WAIT_MESSAGE", "nao coleta conversa diferente");
  listener(resume, {id: "sintetica"}, data => response = data);
  assert.equal(response.code, "RESUMED");
  listener(resume, {id: "sintetica"}, data => response = data);
  assert.equal(response.code, "BUSY", "nao duplica a coleta");
  const result = await done;
  assert.ok(result.ok, result.error);
  assert.equal(writes, 2, "repete a gravacao quando falta confirmacao");
}

(async () => {
  await testNucleo();
  await testWorker();
  for (const kind of ["markdown", "content", "plain"]) await scenario(kind);
  console.log("OK: grade, recortes, numeracao, PDF, fila da extensao (long-poll, abas proprias, auto-atualizacao) e coleta da resposta.");
})().catch(error => { console.error(error); process.exitCode = 1; });
