# Padrão do projeto — Separador de Imagens

## Escopo

Um único programa, todo no terminal e sem interface (pedido do usuário): tirar print da
tela, achar a grade 3x3, gerar o PDF numerado, enviar a página numerada à API da OpenAI,
gravar o TXT e clicar na resposta com o mouse do Windows.

- `servidor_separador.py`: único processo Python 3, somente biblioteca padrão (o Chrome
  invisível usa o Selenium já instalado). Sem servidor HTTP, sem extensão, sem porta aberta.
  Contém o robô, a fila em `pdf/.fluxo.json` e a gravação em `pdf/respostas/`.
- `NUCLEO_JS` (dentro do Python): detecção da grade, recortes, numeração e PDF A4
  (cabeçalho original, recortes numerados, instrução de resposta por número).
  Roda no Chrome invisível via `execute_async_script`, sem abrir página.
- `abrir_separador.bat` e `abrir_separador.sh`: atalhos. `INSTALAR.bat`: requisitos.
- A extensão do ChatGPT Web, o servidor local e o `--modo-site` foram removidos a pedido
  do usuário (versão 6.0.0). Não recriar sem pedido.
- Não reinstalar modelos nem adicionar serviços de descrição, comparação ou OCR
  sem pedido do usuário.

## Print da tela

- Não há `get` a nenhum site: `TelaRobo.open()` deixa o Chrome em `about:blank` (headless)
  só para executar o JS de imagem. Só funciona no Windows.
- `png_tela()` (ctypes/GDI, stdlib) printa a tela virtual inteira a cada `INTERVALO_TELA`
  (5s); os bytes ficam só em memória e o PNG só é recodificado (zlib nível 1) quando os
  pixels mudam. `TELA_TIMEOUT = 0` mantém a vigilância indefinida.
- `LOCALIZAR_JS` tenta a tela inteira; se falhar, procura 9 blocos não brancos quase
  quadrados, do mesmo tamanho, em 3x3 e com vãos estreitos (recusa pedaços de grades
  maiores); por fim, cada região quase branca conectada (até 8). Devolve o recorte do
  desafio, que segue para `GERAR_PDF_JS` inalterado. Sem grade, o terminal mostra o motivo
  (só medidas) a cada 20s no máximo. Com mais de uma grade, usa a de fotos maiores.
- Só entrega um recorte repetido em duas prints seguidas (`CONFIRMAR_TELA`, 1s); após uma
  resposta, espera `ESPERA_APOS_RESPOSTA` (2s). Tela bloqueada não encerra a vigilância.
- Produção (programa aberto por muito tempo, sem ninguém no terminal): 2 falhas seguidas do
  Chrome invisível o reabrem sozinho (`reiniciar_chrome`; desiste após `MAX_REINICIOS`); sem
  desafio na tela, ele é renovado a cada `RENOVAR_CHROME` (2h); `gerar_pdf` reabre e tenta
  mais uma vez. Os avisos internos "Retrying" do Selenium ficam ocultos.
- Se o desafio já respondido continuar igual por `REPETIR_MESMO` (15s), responde de novo; se
  a grade ficar nas mesmas 9 posições com algum detalhe mudando, aceita após `MAX_MUDANCAS`
  prints. As esperas avisam no terminal (nunca ficar preso em silêncio).

## Cliques

- As respostas movem o mouse real do Windows (`clique_humano`) nas posições da captura.
  Uma grade de outro aplicativo pode acionar o fluxo, e a janela sob o cursor pode mudar
  antes da resposta.
- O botão de confirmar é o maior bloco colorido largo logo abaixo da grade, do meio para
  a direita (sem OCR); sem achar, usa a proporção 0.872/0.953. Depois de marcar as imagens,
  `botao_atual()` tira uma print nova e procura o botão de novo (`BOTAO_JS`).

## API da OpenAI (pedido explícito do usuário)

- A chave em `chave_openai.txt` ou `OPENAI_API_KEY` é obrigatória: sem ela o programa
  avisa e não abre. A página numerada (JPEG) vai para `/v1/responses` (urllib, stdlib)
  junto com `PROMPT_API`; o PDF fica só no histórico. Modelo: `--modelo` (padrão `gpt-4.1`).
- `PROMPT_API` pede só as imagens que atendem exatamente ao cabeçalho (pode ser só uma).
- A chave nunca é exibida, registrada em log nem versionada (`.gitignore`).
- Testes usam servidor falso local; nunca a API real com dados privados.

## Regras

- Não adicionar dependências externas (o Chrome invisível usa o Selenium já existente).
- Não abrir portas nem servidores.
- Preservar `pdf/.fluxo.json` e `pdf/respostas/`; não apagar histórico à mão.
- Só apagar PDF/TXT antigos quando uma nova resposta for gravada; em caso de
  falha, preservar os arquivos anteriores.
- Não abrir nem enviar imagens privadas de teste; usar dados sintéticos.
- Preferir alterações pequenas e mostrar o diff antes de aplicar.
- Manter a lógica de detecção, recorte, numeração e geração do PDF estável.
- Não alterar funcionalidades fora do pedido.
- Não criar processos auxiliares (vigias, agentes, tarefas agendadas): o fluxo
  vive dentro do único programa. `pdf/.programa.lock` impede abrir duas vezes.

## Fluxo do robô

- O robô inicia no mesmo processo; a entrada manual no terminal e a automática
  usam a mesma fila. Não injetar teclas em outro terminal. `s` ou Ctrl+C encerram.
- Cada captura tem identidade única; resposta de captura antiga não é executada.
- Grade não reconhecida: nova captura automática (até 3 tentativas).
- Falha na API (ou resposta sem números): nova captura automática, no máximo 2 vezes seguidas.
- Aviso que pede `p` ou números volta a vigiar sozinho após `RETOMAR_SOZINHO` (30s) sem resposta.
- Erro inesperado no robô (mouse, Chrome...) não encerra o programa: avisa, espera
  `ESPERA_RECOMECAR` (10s), reabre o Chrome e recomeça. Só para se nem abrir (Selenium ausente).
- Tarefas de execuções anteriores ficam no histórico como erro, sem reenviar.

## Validação

`py -m py_compile servidor_separador.py` e `py test_fluxo.py`, sempre sintéticos.

## Sigilo das imagens

- As imagens deste projeto sao secretas. Nao abrir, visualizar, transcrever, analisar ou expor seu conteudo, nem os PDFs que as contenham, durante manutencao e testes.
- Validar somente com dados sinteticos. Nao enviar arquivos privados em testes. O envio pela API permanece restrito as tarefas iniciadas pelo usuario.
