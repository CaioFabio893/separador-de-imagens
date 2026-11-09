# Padrão do projeto — Separador de Imagens

## Visão geral

- `servidor_separador.py` — servidor local em Python, somente biblioteca
  padrão. Serve os arquivos da pasta e faz a ponte com o Ollama em
  `127.0.0.1:11434`.
- `numerador_matriz.html` — app de página única (HTML/CSS/JS) que detecta a
  grade, recorta, numera e exporta as células.
- `abrir_separador.sh` — atalho de inicialização no Linux.
- Ollama com os modelos `moondream` (legendas) e `qwen2.5:3b`
  (comparação). Tesseract (`por+eng`) para o OCR do cabeçalho.

## Como validar

```bash
py -m py_compile servidor_separador.py     # Windows
python3 -m py_compile servidor_separador.py  # Linux

py servidor_separador.py                   # Windows
python3 servidor_separador.py              # Linux

curl "http://127.0.0.1:8765/api/ollama/status?model=moondream"
```

Teste manual: abrir `http://127.0.0.1:8765`, importar uma captura com grade
3×3, conferir os nove recortes, a leitura do cabeçalho e as legendas.

## Regras de código

- Python 3 somente stdlib. Não adicionar dependências externas.
- Servidor local em `127.0.0.1`, sem CORS amplo.
- Respostas JSON sempre com as chaves: `servidor`, `ok`, `code`, `message`.
- Erros: nunca vazar stack trace; mensagem em português e acionável.
- Validação de entrada centralizada em helpers, sem repetir `try/except`.
- Preferir `logging` a `print` solto no servidor.
- Nomes claros, type hints e docstring curta quando ajudar a leitura.
- Não alterar rotas, campos JSON, IDs do HTML nem chaves de `localStorage`.
- Refatorar em passos pequenos: um diff por vez, no máximo ~150 linhas.
- Se algo estiver fora do escopo pedido, apenas listar. Não corrigir.

## Área congelada

Não refatorar, não mover e não "melhorar":

- rota `/api/banner/compare`;
- variável `generic_silhouette` e o regex de silhouette;
- instruções `MATCH` / `DIFFERENT` / `UNKNOWN`;
- fluxo "Sugerir respostas" no HTML.

## Fluxo de refatoração

1. Mapear funções/classes e apontar duplicação e funções longas. Não editar.
2. Propor plano em passos pequenos, com ganho e risco. Não editar.
3. Executar um passo por mensagem, mostrando o diff antes de aplicar.
4. Rodar `py_compile` (e `ruff check .` se existir) ao fim de cada passo.
5. Só concluir com comportamento observável inalterado.
