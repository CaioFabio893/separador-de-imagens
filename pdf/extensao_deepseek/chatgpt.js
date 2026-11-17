"use strict";
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
