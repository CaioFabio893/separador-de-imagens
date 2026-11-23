# AGENTS.md

Instruções do projeto: ver `CLAUDE.md`.

- Manter um único fluxo, todo no terminal e sem interface: print da tela, gerar o PDF numerado, enviar à API da OpenAI, gravar o TXT e clicar na resposta.
- Python 3 somente stdlib; não adicionar dependências (o Chrome invisível usa o Selenium já existente).
- Sem servidor, sem porta aberta e sem extensão (removidos a pedido do usuário).
- Preservar `pdf/.fluxo.json` e `pdf/respostas/`; só apagar PDF/TXT antigos quando a nova resposta for gravada.
- Não abrir nem enviar imagens privadas; validar com dados sintéticos.
- Mostrar o diff antes de aplicar e preferir passos pequenos.
- Validar com `py -m py_compile servidor_separador.py` e `py test_fluxo.py`.
- Não criar processos auxiliares (vigias, tarefas agendadas): tudo roda no único programa.
- Não adicionar outros serviços de IA ou OCR sem pedido explícito.


## Sigilo das imagens

- As imagens deste projeto sao secretas. Nao abrir, visualizar, transcrever, analisar ou expor seu conteudo, nem os PDFs que as contenham, durante manutencao e testes.
- Validar somente com dados sinteticos. Nao enviar arquivos privados em testes. O envio pela API permanece restrito as tarefas iniciadas pelo usuario.
