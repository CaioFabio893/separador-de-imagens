# Padrão do projeto — Separador de Imagens

## Escopo

Um único programa, todo no terminal e sem interface (pedido do usuário):
capturar a imagem no robô, gerar o PDF numerado, enviar ao ChatGPT Web pela
extensão, gravar o TXT e entregar a resposta ao robô.

- `servidor_separador.py`: único processo Python 3, somente biblioteca padrão
  (o robô reutiliza o Selenium), em `127.0.0.1`. Contém o robô, a fila em
  `pdf/.fluxo.json`, a gravação em `pdf/respostas/` e o código da extensão.
- `NUCLEO_JS` (dentro do Python): detecção da grade, recortes, numeração e PDF A4
  (cabeçalho original, recortes numerados, instrução de resposta por número).
  Roda no navegador do robô via `execute_async_script`, sem abrir página.
- `pdf/extensao_deepseek`: gerada a partir de `EXTENSION_FILES` no Python; envia o
  PDF ao ChatGPT Web e devolve a resposta. Recarrega-se sozinha quando a versão muda.
- `abrir_separador.bat` e `abrir_separador.sh`: atalhos. `INSTALAR.bat`: requisitos.
- Não reinstalar modelos nem adicionar serviços de descrição, comparação ou OCR
  sem pedido do usuário.

## Rotas

- `GET /api/status`: o servidor está pronto.
- `GET /api/vigia/next?espera=N`: próxima tarefa; espera até N s (long-poll).
- `POST /api/vigia/opened|payload|dispatch|result|progress|error`.
- Todas as rotas `/api/vigia/*` exigem token (`X-Vigia-Token`),
  `X-Vigia-Provider: chatgpt_web` e origem de extensão. Não há outras rotas.

## Regras

- Não adicionar dependências externas ao servidor (o robô usa o Selenium já existente).
- Não abrir o servidor na rede nem adicionar CORS.
- Respostas JSON incluem `servidor`, `ok`, `code` e `message`.
- Preservar `pdf/.fluxo.json` e `pdf/respostas/`; não apagar histórico à mão.
- Só apagar PDF/TXT antigos quando uma nova resposta for gravada; em caso de
  falha, preservar os arquivos anteriores e os PDFs ainda na fila.
- Não abrir nem enviar imagens privadas de teste; usar dados sintéticos.
- Preferir alterações pequenas e mostrar o diff antes de aplicar.
- Manter a lógica de detecção, recorte, numeração e geração do PDF estável.
- Não alterar funcionalidades fora do pedido.
- Não criar processos auxiliares (vigias, agentes, tarefas agendadas): o fluxo
  vive dentro do único programa.

## Fluxo do robô

- O robô inicia no mesmo processo; a entrada manual no terminal e a automática
  usam a mesma fila. Não injetar teclas em outro terminal.
- Cada captura tem identidade única; resposta de captura antiga não é executada.
- Grade não reconhecida: nova captura automática (até 3 tentativas).
- Falha no ChatGPT: nova captura automática, no máximo 2 vezes seguidas.

## Validação

`py -m py_compile servidor_separador.py`, `py test_fluxo.py`,
`node test_extensao.js` e `node test_chatgpt_web.js`, sempre sintéticos.

## Extensão ChatGPT Web (pedido explícito do usuário)

- O destino é `https://chatgpt.com/`, sem API da OpenAI e sem cópia manual.
- O caminho `pdf/extensao_deepseek` e `config_local.js` são mantidos para preservar
  a instalação e o token. Os arquivos são background.js, chatgpt.js e manifest.json.
- Só atuar em uma aba criada e registrada pela própria tarefa. Só fechá-la
  depois de confirmar a gravação do TXT. Não ler conversas anteriores.
- Não considerar pausa na geração como fim da resposta.


## Sigilo das imagens

- As imagens deste projeto sao secretas. Nao abrir, visualizar, transcrever, analisar ou expor seu conteudo, nem os PDFs que as contenham, durante manutencao e testes.
- Validar somente com dados sinteticos. Nao enviar arquivos privados em testes. O envio pelo fluxo normal da extensao permanece restrito as tarefas iniciadas pelo usuario.
