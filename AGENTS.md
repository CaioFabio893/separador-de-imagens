# AGENTS.md

Instruções do projeto: ver `CLAUDE.md`.

- Manter um único fluxo: gerar o PDF numerado, enviar ao DeepSeek e gravar a resposta em TXT.
- Python 3 somente stdlib; não adicionar dependências.
- Servidor local em `127.0.0.1`, sem CORS amplo; rotas da extensão exigem token.
- JSON sempre com `servidor`, `ok`, `code`, `message`.
- Preservar dados e chaves de armazenamento das imagens, marcações e ajustes.
- Preservar `pdf/.fluxo.json` e `pdf/respostas/`; só apagar PDF/TXT antigos quando a nova resposta for gravada.
- Não abrir nem enviar imagens privadas; validar com dados sintéticos.
- Mostrar o diff antes de aplicar e preferir passos pequenos.
- Validar com `py -m py_compile servidor_separador.py`.
- Não criar processos auxiliares (vigias, tarefas agendadas): tudo roda no único servidor.
- Não adicionar outros serviços de IA ou OCR sem pedido explícito.
- Prompt de refatoração: `PROMPT_REFATORACAO.md`.
