"use strict";
(() => {
  const APP="separador";
  const reply=(ok,code,message,extra={})=>({servidor:APP,ok,code,message,...extra});
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  let running=false, current=null;
  function composer() {
    let node=document.querySelector('textarea#chat-input,textarea[placeholder],textarea');
    for(let depth=0;node&&depth<7;depth++,node=node.parentElement) {
      if(node.querySelector('input[type="file"]')&&node.querySelector('textarea')) return node;
    }
    return null;
  }
  // Ultimo bloco de texto da resposta, ignorando a linha do proprio pedido.
  // O painel de "pensamento" fica de fora: so serve de ultimo recurso.
  function lastAnswer(marca) {
    const rows=Array.from(document.querySelectorAll("[data-virtual-list-item-key]"));
    for(let i=rows.length-1;i>=0;i--) {
      const row=rows[i];
      if(marca&&(row.innerText||"").includes(marca))continue;
      const final=row.querySelector(".ds-assistant-message-main-content");
      if(final&&(final.innerText||"").trim())return final;
      const fora=Array.from(row.querySelectorAll(".ds-markdown"))
        .filter(n=>!n.closest('[class*="think"]')&&(n.innerText||"").trim());
      if(fora.length)return fora.at(-1);
    }
    const blocks=Array.from(document.querySelectorAll(".ds-markdown"))
      .filter(n=>!n.closest('[class*="think"]')&&(n.innerText||"").trim());
    return blocks.at(-1)||null;
  }
  function qualquerMarkdown() {
    const blocks=Array.from(document.querySelectorAll(".ds-markdown"))
      .filter(n=>(n.innerText||"").trim());
    return blocks.at(-1)||null;
  }
  // Trecho de texto da tela para explicar uma falha (nunca inclui imagens).
  function pista() {
    const partes=[];
    const resposta=qualquerMarkdown();
    if(resposta)partes.push("Resposta: "+resposta.innerText.trim().replace(/\s+/g," ").slice(0,120));
    const corpo=(document.body.innerText||"").replace(/\s+/g," ").trim();
    if(corpo)partes.push("Tela: "+corpo.slice(0,120));
    return partes.length?" | "+partes.join(" | "):"";
  }
  function bloqueio() {
    const texto=document.body.innerText||"";
    const achado=texto.match(/(atingiu o limite|limite de mensagens|too many requests|rate limit|服务器繁忙|请稍后再试|try again later|server is busy|sistema ocupado)/i);
    return achado?achado[0]:"";
  }
  function button(root) {return root?.querySelector('[role="button"].ds-button--primary');}
  function arrow(control) {
    return !!control && (Array.from(control.querySelectorAll("svg path")).some(p=>(p.getAttribute("d")||"").startsWith("M8.3125 0.980206")) ||
      /^(enviar|send)$/i.test(control.getAttribute("aria-label")||""));
  }
  function disabled(control) {
    return !control||control.classList.contains("ds-button--disabled")||control.getAttribute("aria-disabled")==="true"||control.disabled;
  }
  function send(code,message,extra={}) {
    return chrome.runtime.sendMessage(reply(true,code,message,{id:current.id,...extra}));
  }
  async function until(check,ms,description) {
    const end=Date.now()+ms;
    while(Date.now()<end) {
      const value=check();if(value)return value;
      await sleep(500);
    }
    throw Error(description);
  }
  function uploadError(root) {
    return /no text extracted|nenhum texto|não foi possível|falha no (envio|upload|processamento)|upload failed|file upload failed|unsupported file|arquivo não suportado|excedeu|exceeds|limit reached/i.test(root.innerText);
  }
  async function process(job) {
    let submitted=false;
    try {
      const root=await until(composer,30000,"Não encontrei o formulário de envio. Verifique o login no DeepSeek.");
      const input=root.querySelector('input[type="file"]');
      const textarea=root.querySelector("textarea");
      if(document.querySelector(".ds-assistant-message-main-content")||textarea.value.trim()||input.files.length) {
        throw Error("A conversa não está vazia. Nenhuma mensagem foi substituída.");
      }
      // Operate only this new, explicitly assigned chat. Never use sidebar/history.
      const search=Array.from(root.querySelectorAll('[aria-pressed]')).find(e=>/pesquisa|search|搜索/i.test(e.innerText));
      // The prompt already forbids web search; the toggle is only turned off when it can be found.
      if(search && search.getAttribute("aria-pressed")==="true") {
        search.click();
        await until(()=>search.getAttribute("aria-pressed")==="false",5000,"Não foi possível desligar a pesquisa na web.");
      }
      await send("PROGRESS","Anexando PDF.");
      const binary=atob(job.data);const data=new Uint8Array(binary.length);
      for(let i=0;i<binary.length;i++)data[i]=binary.charCodeAt(i);
      const transfer=new DataTransfer();
      transfer.items.add(new File([data],job.name,{type:"application/pdf"}));
      input.files=transfer.files;input.dispatchEvent(new Event("change",{bubbles:true}));
      job.data="";
      // Wait for the actual attachment card and the site's enabled send button.
      await until(()=>composer()?.parentElement?.innerText.includes(job.name),15000,"O DeepSeek não exibiu o PDF anexado.");
      const editor=composer()?.querySelector("textarea");
      if(!editor?.isConnected)throw Error("O formulário mudou durante o upload. Envio interrompido.");
      const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set;
      setter.call(editor,job.prompt);
      editor.dispatchEvent(new Event("input",{bubbles:true}));
      editor.dispatchEvent(new Event("change",{bubbles:true}));
      let stableSince=0;
      await until(()=>{
        const currentRoot=composer();
        if(!currentRoot)return false;
        const scope=currentRoot.parentElement;
        if(uploadError(scope))throw Error("O DeepSeek informou uma falha ao ler o PDF. Confira a aba antes de repetir.");
        const control=button(currentRoot);
        const busy=scope.querySelector('[role="progressbar"],.ds-loading,.ds-loading-indicator');
        if(arrow(control)&&!disabled(control)&&!busy) {
          if(!stableSince)stableSince=Date.now();
          return Date.now()-stableSince>=4000;
        }
        stableSince=0;return false;
      },120000,"O PDF não terminou de carregar em dois minutos. Não houve envio da mensagem.");
      const sendControl=button(composer());
      if(!sendControl?.isConnected||!arrow(sendControl)||disabled(sendControl))throw Error("O botão de enviar mudou. Envio interrompido.");
      const startUrl=location.href;
      sendControl.click();submitted=true;
      // DeepSeek moves a new chat to /a/chat/s/<id> once the message is accepted.
      await until(()=>(location.href!==startUrl && /\/chat\/s\//.test(location.pathname)) ||
        lastAnswer(job.name) ||
        Array.from(document.querySelectorAll("[data-virtual-list-item-key]")).some(row=>row.innerText.includes(job.prompt)),
        60000,"O DeepSeek não confirmou a mensagem na conversa. Confira a aba antes de repetir.");
      await send("PROGRESS","Mensagem confirmada na conversa; aguardando resposta.");
      let previous="", stable=0, ultimoAviso=0;
      const inicioEspera=Date.now();
      const text=await until(()=>{
        if(Date.now()-ultimoAviso>30000) {
          ultimoAviso=Date.now();
          const segundos=Math.round((Date.now()-inicioEspera)/1000);
          send("PROGRESS","Aguardando resposta ("+segundos+"s)."+pista()).catch(()=>{});
        }
        const aviso=bloqueio();
        if(aviso)throw Error("O DeepSeek recusou o envio (" + aviso + ")." + pista());
        // Depois de um minuto sem resposta, aceita o texto do painel de pensamento.
        const answer=lastAnswer(job.name)||(Date.now()-inicioEspera>60000?qualquerMarkdown():null);
        if(!answer)return false;
        const value=answer.innerText.trim();
        const row=answer.closest("[data-virtual-list-item-key]");
        const controls=row?.querySelectorAll('[role="button"]').length||0;
        const control=button(composer());
        // Final answer only: stable text, restored send arrow and final action toolbar.
        // A pause in streaming alone is not accepted as completion.
        if(!value){previous="";stable=0;return false;}
        if(value!==previous) {previous=value;stable=Date.now();return false;}
        const finished=controls>=4 || arrow(control);
        // Finished UI + 5 s of stable text, or 20 s without any change as a fallback.
        if((finished && Date.now()-stable>=5000) || Date.now()-stable>=20000)return value;
        return false;
      },10*60*1000,"Não consegui confirmar o fim da resposta em 10 minutos."+pista());
      // Retain the result until the monitor confirms it was written to disk.
      for(;;) {
        try {
          const response=await send("RESULT","Resposta pronta.",{text,chatUrl:location.href});
          if(response?.ok)break;
        } catch {}
        await sleep(5000);
      }
    } catch(error) {
      let mensagem=error.message||"Falha no DeepSeek.";
      if(!mensagem.includes("| Tela:"))mensagem+=pista();
      try {await send("ERROR",mensagem+(submitted?" O PDF pode já ter sido recebido.":""));}catch {}
    } finally {running=false;}
  }
  chrome.runtime.onMessage.addListener((message,sender,respond)=>{
    if(sender.id!==chrome.runtime.id||sender.tab || (sender.url && sender.url!==chrome.runtime.getURL("background.js"))||message?.servidor!==APP)return;
    if(message.code==="PING") {
      respond(reply(!!composer(),"READY","Disponibilidade do formulário."));return;
    }
    if(message.code==="PROCESS") {
      if(running){respond(reply(false,"BUSY","Já existe um PDF sendo processado nesta aba."));return;}
      if(typeof message.id!=="string"||typeof message.data!=="string"||message.data.length>29*1024*1024||!/\.pdf$/i.test(message.name)) {
        respond(reply(false,"INVALID_JOB","Tarefa inválida."));return;
      }
      running=true;current=message;respond(reply(true,"ACCEPTED","PDF recebido pela extensão."));
      void process(message);
    }
  });
})();
