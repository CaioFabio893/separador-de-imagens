# Separador de Imagens → PDF → DeepSeek

Um único aplicativo: escolher as imagens, gerar o PDF numerado, enviar esse PDF
ao DeepSeek e guardar a resposta em TXT.

1. Abra `abrir_separador.bat` no Windows ou execute `bash abrir_separador.sh` no Linux.
2. Selecione uma ou mais imagens contendo o retângulo do cabeçalho e a grade.
3. Clique em **Gerar PDF e enviar ao DeepSeek**.

O que acontece em seguida, sem mais cliques:

- O PDF é salvo em `pdf/`.
- A extensão `pdf/extensao_deepseek` (já instalada no Chrome) abre o DeepSeek,
  anexa o PDF, envia o pedido e espera a resposta terminar.
- A resposta é gravada em `pdf/respostas/` como TXT e aparece na própria página,
  com botões para copiar e para abrir a pasta.
- Quando a nova resposta chega, o PDF e o TXT anteriores são apagados.
  Se a tarefa falhar, os arquivos anteriores são preservados.

O PDF contém uma página A4 por imagem selecionada, com o cabeçalho original, os
recortes numerados em ordem de leitura e o texto:

**Responda apenas com o número correspondente à imagem.**

*Sua resposta deve conter apenas dígitos e vírgulas.*

Os números ficam abaixo de cada recorte. A numeração recomeça em 1 em cada página.
Se a grade não for detectada, o programa identifica o arquivo que precisa ser corrigido.

## Como está montado

- `servidor_separador.py`: um só processo, em `127.0.0.1` (portas 8765–8784).
  Serve a interface, recebe o PDF (`/api/pdf`), informa o andamento (`/api/fluxo`),
  abre a pasta de respostas (`/api/abrir`) e atende a extensão em `/api/vigia/*`.
  Também encerra tarefas travadas; não existe mais um vigia separado.
- `numerador_matriz.html`: detecta as grades, monta o PDF no navegador e mostra
  o andamento do fluxo. As imagens nunca saem do computador.
- `pdf/extensao_deepseek`: extensão do Chrome que conversa com o servidor local
  usando um token em `config_local.js` e a rota `chat.deepseek.com`.
- `pdf/.fluxo.json`: fila e histórico das tarefas (PDF, estado e TXT gerado).
- `pdf/respostas/`: respostas em TXT.

Todo o processamento de imagens e de PDF é feito no navegador. Python 3 somente
com biblioteca padrão. O servidor não abre para a rede e não usa CORS amplo.

## Se algo não funcionar

- **"Extensão não conectada"**: abra o Chrome pelo atalho do programa e confira
  se a extensão `PDF → DeepSeek` está ativa em `chrome://extensions`.
- **DeepSeek pedindo login**: entre na conta na aba aberta; o fluxo continua sozinho.
- **Falha no meio**: a página mostra o motivo e o PDF anterior é preservado.
  Gere o PDF de novo para repetir.

## Validação

```text
py -m py_compile servidor_separador.py
```

Depois, abra a página e confira `/api/status` e `/api/fluxo`.
