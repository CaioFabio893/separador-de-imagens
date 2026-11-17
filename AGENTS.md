# AGENTS.md

Instruções do projeto: ver `CLAUDE.md`.

- Manter um único fluxo, todo no terminal e sem interface: capturar, gerar o PDF numerado, enviar ao ChatGPT Web, gravar o TXT e responder no robô.
- Python 3 somente stdlib; não adicionar dependências (o robô usa o Selenium já existente).
- Servidor local em `127.0.0.1`, sem CORS; rotas da extensão exigem token.
- JSON sempre com `servidor`, `ok`, `code`, `message`.
- Preservar `pdf/.fluxo.json` e `pdf/respostas/`; só apagar PDF/TXT antigos quando a nova resposta for gravada.
- Não abrir nem enviar imagens privadas; validar com dados sintéticos.
- Mostrar o diff antes de aplicar e preferir passos pequenos.
- Validar com `py -m py_compile servidor_separador.py`, `py test_fluxo.py`, `node test_extensao.js` e `node test_chatgpt_web.js`.
- Não criar processos auxiliares (vigias, tarefas agendadas): tudo roda no único programa.
- Não adicionar outros serviços de IA ou OCR sem pedido explícito.


## Sigilo das imagens

- As imagens deste projeto sao secretas. Nao abrir, visualizar, transcrever, analisar ou expor seu conteudo, nem os PDFs que as contenham, durante manutencao e testes.
- Validar somente com dados sinteticos. Nao enviar arquivos privados em testes. O envio pelo fluxo normal da extensao permanece restrito as tarefas iniciadas pelo usuario.
