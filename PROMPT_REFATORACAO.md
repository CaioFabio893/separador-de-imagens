# Prompt de refatoração — qualidade de código

Prompts prontos para colar no Claude Code (ou em outro agente). O foco é
**somente qualidade de código**, com limite explícito no trecho congelado
descrito em `CLAUDE.md`.

## Prompt principal (colar inteiro)

```text
Contexto: este repositório é o "Separador de Imagens" — servidor local em
Python (somente biblioteca padrão) que serve numerador_matriz.html, e um app
de página única em HTML/JS que recorta grades de imagens, numera células,
gera legendas via Ollama e faz OCR via Tesseract.

Objetivo: melhorar SOMENTE a qualidade do código. Não mudar comportamento,
não mudar endpoints, não mudar a interface, não mudar textos visíveis.

Limites rígidos:
- NÃO altere nada relacionado a /api/banner/compare, à variável
  generic_silhouette, às instruções de "silhouette"/MATCH/DIFFERENT/UNKNOWN
  nem ao fluxo "Sugerir respostas". Trate esse bloco como congelado.
- NÃO adicione dependências externas. O servidor deve continuar rodando com
  python3 puro (só stdlib).
- NÃO renomeie rotas, campos JSON, chaves de localStorage nem IDs do HTML.
- NÃO reformate o arquivo inteiro de uma vez. Um passo por vez.
- Se encontrar algo fora do escopo, apenas liste no final. Não corrija.

O que quero, nesta ordem:
1. Mapa do código: liste as funções/classes dos dois arquivos com uma linha
   cada, apontando duplicação, funções longas e responsabilidades misturadas.
2. Plano de refatoração em passos pequenos e independentes, cada passo com
   o ganho esperado e o risco. Não edite nada ainda.
3. Depois que eu aprovar o plano, execute UM passo por mensagem, mostrando
   o diff antes de aplicar.
4. Em cada passo: nomes claros, type hints, docstring curta quando ajudar,
   validação de entrada centralizada, erros com mensagem consistente e
   logging em vez de print solto.
5. Ao final de cada passo, rode: python -m py_compile servidor_separador.py
   (e ruff check . se estiver instalado). Se falhar, corrija antes de seguir.

Critérios de aceite:
- Nenhuma mudança de comportamento observável.
- py_compile sem erro; app abre em 127.0.0.1:8765 e recorta/numera uma
  imagem de teste.
- Diff revisável: nenhum passo com mais de ~150 linhas alteradas.
- Nenhum arquivo novo sem eu pedir.

Comece pelo item 1.
```

## Versão em etapas (uma por sessão)

```text
1) Liste as funções de servidor_separador.py com responsabilidade e tamanho.
   Aponte duplicação e funções acima de 40 linhas. Não edite nada.
```

```text
2) Extraia a validação de corpo/JSON/Content-Length de api_describe e
   api_banner_ocr para um helper único. Só isso. Mostre o diff.
```

```text
3) Centralize as respostas de erro do servidor em um helper
   json_error(handler, status, code, message) e aplique nas rotas, sem
   mudar nenhuma mensagem nem status existente.
```

```text
4) No numerador_matriz.html, separe o JavaScript em módulos por
   responsabilidade (estado, render, API, exportação) usando <script type="module">.
   Mantenha todos os IDs e comportamentos. Faça em 2 passos.
```

```text
5) Adicione type hints e docstrings curtas nas funções públicas do servidor.
   Sem alterar lógica.
```

## Observação

Se o pedido for melhorar justamente o bloco de comparação/silhueta, o agente
vai recusar — e não é falta de prompt. Para esse trecho, o caminho é remover
a lógica específica de CAPTCHA e manter só o que é genérico.
