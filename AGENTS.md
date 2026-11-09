# AGENTS.md

Instruções do projeto: ver `CLAUDE.md`. Resumo aplicável a qualquer agente:

- Python 3 somente stdlib; não adicionar dependências.
- Servidor local em `127.0.0.1`, sem CORS amplo.
- JSON sempre com `servidor`, `ok`, `code`, `message`.
- Não alterar rotas, campos JSON, IDs do HTML nem chaves de `localStorage`.
- Refatorar em passos pequenos (~150 linhas por diff), mostrando o diff antes.
- Validar com `py -m py_compile servidor_separador.py`.
- Área congelada: `/api/banner/compare`, `generic_silhouette`,
  `MATCH`/`DIFFERENT`/`UNKNOWN` e o fluxo "Sugerir respostas".
- Prompt de refatoração pronto: `PROMPT_REFATORACAO.md`.
