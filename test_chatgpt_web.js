"use strict";
// Teste do DOM no Chrome isolado. Nenhum site, perfil ou arquivo privado e usado.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {pathToFileURL} = require("node:url");
const {spawnSync} = require("node:child_process");
const root = path.basename(__dirname) === "chatgpt-migracao" ? path.resolve(__dirname, "../..") : __dirname;
const source = fs.readFileSync(path.join(root, "servidor_separador.py"), "utf8");
const contentScript = source.split("EXT_CHATGPT = r'''", 2)[1].split("'''", 1)[0];
const browser = [process.env.CHROME_TEST_BIN,
  path.join(process.env.PROGRAMFILES || "C:/Program Files", "Google/Chrome/Application/chrome.exe"),
  path.join(process.env["PROGRAMFILES(X86)"] || "C:/Program Files (x86)", "Google/Chrome/Application/chrome.exe"),
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find(candidate => candidate && fs.existsSync(candidate));
if (!browser) throw Error("Chrome/Chromium ausente. Informe CHROME_TEST_BIN para rodar o teste isolado.");
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "chatgpt-ext-test-"));
const embeddedCode = JSON.stringify(contentScript).replace(/</g, "\\u003c");
const fixture = `<!doctype html><meta charset="utf-8"><title>Teste sintetico da extensao</title>
<style>body{font:16px sans-serif}#prompt-textarea{min-height:50px;width:500px;border:1px solid}button{padding:10px}.markdown{min-height:24px}</style>
<div id="fixture"></div><output id="test-result">pendente</output><script>
const source = ${embeddedCode};
const checks = [];
function check(value, message) { if (!value) throw Error(message); checks.push(message); }
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const job = () => ({servidor:"separador",code:"PROCESS",provider:"chatgpt_web",id:"tarefa-sintetica",name:"sintetico.pdf",prompt:"Responda somente os numeros do PDF sintetico.",data:btoa("%PDF-1.4\\n%%EOF\\n")});
const sender = {id:"extensao-sintetica",url:"chrome-extension://extensao-sintetica/background.js"};
function setup(kind="editable", failUpload=false) {
  delete globalThis.__separadorChatgptLoaded;
  const element = document.getElementById("fixture");
  element.innerHTML = '<form><input type="file" accept=".pdf"><div id="attachment"></div>' +
    (kind.endsWith("textarea") ? '<textarea id="prompt-textarea"></textarea>' : '<div id="prompt-textarea" contenteditable="true"></div>') +
    '<button type="button" data-testid="send-button" disabled>Enviar</button></form><section id="conversation"></section>';
  let listener, uploads=0, sends=0, results=0, lastText="", ended=false, registrations=0;
  let finish;
  const completed = new Promise(resolve => finish=resolve);
  globalThis.chrome = {runtime:{id:sender.id,getURL:name=>"chrome-extension://extensao-sintetica/"+name,
    onMessage:{addListener: fn=>{listener=fn;registrations++;}},
    sendMessage: async message=>{
      if (message.code === "RESULT") {
        results++; lastText=message.text;
        check(ended, "nao captura resposta parcial enquanto ainda esta gerando");
        if (results === 1) return {ok:false,code:"FAILED"};
        finish({ok:true});
        return {ok:true,code:"SAVED"};
      }
      if (message.code === "ERROR") finish({ok:false,message:message.message});
      return {ok:true,code:"ACK"};
    }}};
  const field = document.getElementById("prompt-textarea");
  if (kind.startsWith("draft")) { if (field.tagName==="TEXTAREA") field.value=job().prompt; else field.textContent=job().prompt; }
  if (kind === "semantic") {
    field.removeAttribute("id");field.setAttribute("role","textbox");field.style.cssText="min-height:50px;width:500px;border:1px solid";
  }
  const send = element.querySelector('[data-testid="send-button"]');
  if (["nested", "localized", "stale"].includes(kind)) {
    const inner = document.createElement("div");inner.dataset.type="unified-composer";
    field.replaceWith(inner);inner.appendChild(field);
    if (kind === "localized") {
      const form=element.querySelector("form");
      const outer=document.createElement("div");outer.dataset.testid="composer";
      form.replaceWith(outer);outer.append(...form.childNodes);
      send.removeAttribute("data-testid");send.setAttribute("aria-label","Enviar");
    }
    if (kind === "nested") {
      send.removeAttribute("data-testid");send.id="composer-submit-button";
    }
    if (kind === "stale") {
      const stale=send.cloneNode(true);send.before(stale);
      stale.addEventListener("click",()=>{throw Error("botao antigo nao deve ser acionado");});
    }
  }
  element.querySelector('input[type="file"]').addEventListener("change", event=>{
    uploads++;
    check(event.target.files.length===1, "anexa um unico PDF");
    check(event.target.files[0].name==="sintetico.pdf", "mantem o nome do PDF da tarefa");
    if (kind==="anexo-lento" && uploads===1) return; // o site ignora a primeira tentativa
    document.getElementById("attachment").textContent=event.target.files[0].name;
    if (failUpload) {
      const alert=document.createElement("div");alert.role="alert";alert.textContent="Upload limit reached";
      element.appendChild(alert);
    }
  });
  field.addEventListener("input", ()=>{send.disabled=false;});
  send.addEventListener("click", ()=>{
    sends++;
    const value = field.tagName==="TEXTAREA" ? field.value : field.innerText;
    check(value===job().prompt, "preenche o prompt no editor " + kind);
    const conversation=document.getElementById("conversation");
    conversation.innerHTML='<div data-message-author-role="user"></div><article data-testid="conversation-turn-1"><div data-message-author-role="assistant"><div data-testid="reasoning"><div class="markdown">pensamento sintetico</div></div></div><button type="button" data-testid="copy-turn-action-button">Copiar</button></article><button type="button" data-testid="stop-button">Parar</button>';
    conversation.querySelector('[data-message-author-role="user"]').textContent=value;
    // Interface nova: sem os marcadores de autor e com outro nome no botao de copiar.
    const semMarcadores=()=>{
      if(kind==="so-texto"){ // nada reconhecivel: sem papeis, sem .markdown, botoes com outros nomes
        conversation.querySelectorAll('[data-message-author-role]').forEach(n=>n.removeAttribute('data-message-author-role'));
        conversation.querySelectorAll('.markdown').forEach(n=>n.className='bloco');
        conversation.querySelectorAll('[data-testid="copy-turn-action-button"]').forEach(b=>{b.removeAttribute('data-testid');b.setAttribute('aria-label','Mais');});
        return;
      }
      if(kind!=="sem-marcadores")return;
      conversation.querySelectorAll('[data-message-author-role]').forEach(n=>n.removeAttribute('data-message-author-role'));
      conversation.querySelectorAll('[data-testid="copy-turn-action-button"]').forEach(b=>{b.removeAttribute('data-testid');b.setAttribute('aria-label','Copiar mensagem');}); };
    semMarcadores();
    if (field.tagName==="TEXTAREA") field.value=""; else field.replaceChildren();
    // Pausa longa com pensamento e toolbar visivel; nao deve concluir.
    setTimeout(()=>{
      // "barra-fora": bloco sem article e barra de botoes depois dele, como na interface nova.
      const article=document.createElement(kind==="barra-fora"?"div":"article");article.setAttribute(kind==="barra-fora"?"data-turno":"data-testid","conversation-turn-2");
      article.innerHTML='<div data-message-author-role="assistant"><div data-testid="message-content"><div class="markdown">2</div></div></div><button type="button" data-testid="copy-turn-action-button">Copiar</button>';
      conversation.appendChild(article); semMarcadores(); if(kind==="barra-fora"){const barra=article.querySelector("button");article.after(barra);}
    },4000);
    setTimeout(()=>{
      conversation.querySelector('[data-testid="conversation-turn-2"] .markdown, [data-turno] .markdown, [data-testid="conversation-turn-2"] .bloco').textContent="2,9";
      conversation.querySelector('[data-testid="stop-button"]').remove();ended=true;
    },12000);
  });
  new Function("location",source)({origin:"https://chatgpt.com"});
  return {completed, call:(message,who=sender)=>{let response;listener(message,who,value=>response=value);return response;},
    counters:()=>({uploads,sends,results,lastText,registrations}),field};
}
async function tests() {
  let test=setup();
  new Function("location",source)({origin:"https://chatgpt.com"});
  check(test.counters().registrations===1,"reconectar o script nao duplica os ouvintes");
  check(test.call({servidor:"separador",code:"PING"},{...sender,url:"chrome-extension://extensao-sintetica/"}).ok,"aceita a mensagem do proprio worker com URL de origem");
  const foreign=test.call(job(),{...sender,id:"outra-extensao"});
  check(foreign===undefined,"ignora mensagens de outra extensao");
  check(!test.call({...job(),provider:"deepseek"}).ok,"recusa tarefas do provedor antigo");
  test.field.textContent="rascunho do usuario";
  check(test.call(job()).code==="NOT_EMPTY","preserva rascunho existente sem anexar");
  check(test.counters().uploads===0,"nao toca no anexo em conversa ocupada");
  test.field.textContent="";
  test.field.hidden=true;
  check(!test.call({servidor:"separador",code:"PING"}).ok,"aguarda formulario disponivel");
  for (const kind of ["editable","textarea","semantic","nested","localized","stale","draft","draft-textarea","sem-marcadores","barra-fora","so-texto","anexo-lento"]) {
    test=setup(kind);
    check(test.call(job()).code==="ACCEPTED","aceita a tarefa na conversa nova");
    check(test.call(job()).code==="BUSY","bloqueia envio duplicado na mesma aba");
    const done=await test.completed;
    check(done.ok,done.message || "fluxo concluido no editor " + kind);
    await wait(100);
    const counters=test.counters();
    check(counters.sends===1,"clica em Enviar uma unica vez");
    check(counters.lastText==="2,9","captura somente a resposta final");
    check(counters.results===2,"repete somente a entrega local quando falta confirmacao");
  }
  test=setup("editable",true);
  check(test.call(job()).ok,"inicia teste de limite de upload");
  const failed=await test.completed;
  check(!failed.ok && /limite/.test(failed.message),"informa falha de upload sem copiar texto privado");
  check(test.counters().sends===0,"nao envia quando o site recusa o arquivo");
  return {ok:true,checks:checks.length};
}
tests().then(result=>document.getElementById("test-result").textContent=encodeURIComponent(JSON.stringify(result)))
  .catch(error=>document.getElementById("test-result").textContent=encodeURIComponent(JSON.stringify({ok:false,message:error.message,checks:checks.length})));
</script>`;
try {
  const html = path.join(temporary, "fixture.html");
  fs.writeFileSync(html, fixture);
  const result = spawnSync(browser, ["--headless=new", "--disable-gpu", "--disable-background-networking", "--disable-component-update",
    "--disable-extensions", "--disable-sync", "--no-first-run", "--no-default-browser-check", "--no-proxy-server",
    "--user-data-dir=" + path.join(temporary, "profile"), "--dump-dom", "--virtual-time-budget=900000", pathToFileURL(html).href],
    {encoding:"utf8",timeout:45000,maxBuffer:4*1024*1024,windowsHide:true});
  if (result.error) throw result.error;
  const match = result.stdout.match(/<output id="test-result">([^<]+)<\/output>/);
  assert.ok(match, "Chrome deve devolver o resultado da pagina sintetica");
  assert.notEqual(match[1], "pendente", "Teste deve terminar dentro do tempo virtual");
  const report = JSON.parse(decodeURIComponent(match[1]));
  assert.ok(report.ok, report.message);
  console.log("OK: " + report.checks + " verificacoes no Chrome isolado: anexo, envio, resposta completa, repeticao e erros.");
} finally {
  const resolved = path.resolve(temporary);
  assert.ok(resolved.startsWith(path.resolve(os.tmpdir()) + path.sep) && path.basename(resolved).startsWith("chatgpt-ext-test-"));
  // Somente o perfil descartavel criado por este teste; nunca o perfil do usuario.
  try { fs.rmSync(resolved, {recursive:true,force:true,maxRetries:2,retryDelay:200}); }
  catch { console.error("Perfil de teste ainda ocupado; mantido em " + resolved); }
}
