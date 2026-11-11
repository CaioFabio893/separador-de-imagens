# Padrão do projeto — Separador de Imagens

## Escopo

O aplicativo tem um único fluxo: escolher imagens, gerar o PDF numerado, enviar
esse PDF ao DeepSeek e gravar a resposta em TXT.

- `numerador_matriz.html`: detecta grades, gera o PDF A4 no navegador (cabeçalho
  original, recortes numerados, instrução de resposta por número) e mostra o
  andamento do fluxo e a resposta.
- `servidor_separador.py`: único processo Python 3, somente biblioteca padrão, em
  `127.0.0.1`. Serve a interface, recebe o PDF, mantém a fila em `pdf/.fluxo.json`,
  grava o TXT em `pdf/respostas/` e atende a extensão do Chrome.
- `pdf/extensao_deepseek`: extensão do Chrome que envia o PDF ao DeepSeek e
  devolve a resposta ao servidor.
- `abrir_separador.bat` e `abrir_separador.sh`: atalhos para Windows e Linux.
- O processamento das imagens ocorre no navegador. Não reinstalar modelos nem
  adicionar serviços de descrição, comparação ou OCR sem pedido do usuário.

## Rotas

- `GET /api/status`: o servidor está pronto.
- `POST /api/pdf`: recebe o PDF gerado pela página e cria a tarefa.
- `GET /api/fluxo`: estado da tarefa e texto da resposta para a página.
- `POST /api/abrir`: abre a pasta `pdf/respostas`.
- `/api/vigia/next|opened|payload|dispatch|result|error`: uso exclusivo da
  extensão, protegidas por token (`X-Vigia-Token`) e origem de extensão.

## Regras

- Não adicionar dependências externas ao Python.
- Não abrir o servidor na rede nem adicionar CORS amplo.
- Respostas JSON incluem `servidor`, `ok`, `code` e `message`.
- Preservar o armazenamento de imagens, marcações e ajustes do navegador.
- Preservar `pdf/.fluxo.json` e `pdf/respostas/`; não apagar histórico à mão.
- Só apagar PDF/TXT antigos quando uma nova resposta for gravada; em caso de
  falha, preservar os arquivos anteriores e os PDFs ainda na fila.
- Não abrir nem enviar imagens privadas de teste; usar dados sintéticos.
- Preferir alterações pequenas e mostrar o diff antes de aplicar.
- Manter a lógica de detecção, recorte, numeração e geração do PDF estável.
- Não alterar funcionalidades fora do pedido.
- Não criar processos auxiliares (vigias, agentes, tarefas agendadas): o fluxo
  vive dentro do único servidor.

## Validação

Execute `py -m py_compile servidor_separador.py` no Windows ou
`python3 -m py_compile servidor_separador.py` no Linux.

Confira a sintaxe do JavaScript e a detecção com matrizes sintéticas.
O servidor deve responder em `/api/status` e `/api/fluxo`, entregar a interface
e atender a extensão somente com o token correto.
