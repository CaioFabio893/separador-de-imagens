# Separador de Imagens

Abra uma captura com uma grade de fotos. O Separador detecta a grade,
recorta e numera as células. Você pode ampliar os recortes, adicionar
marcações e exportar a imagem numerada ou um ZIP com os recortes e as
coordenadas.

## Como rodar no Linux

Você precisa de Git (ou do ZIP do repositório), Python 3, Bash, curl e um
navegador. O servidor usa a biblioteca padrão do Python.

```bash
git clone https://github.com/CaioFabio893/separador-de-imagens.git
cd separador-de-imagens
bash abrir_separador.sh
```

O atalho abre o endereço local do app. Se precisar iniciar o servidor à
mão, rode `python3 servidor_separador.py` na pasta do projeto e abra o
endereço que aparecer no terminal. A porta padrão é 8765. No Windows,
use `py servidor_separador.py` com o Python 3 instalado.

Você também pode abrir `numerador_matriz.html` diretamente no navegador
para recortar, numerar e exportar. As descrições automáticas usam o
endereço do servidor local.

## Descrições automáticas

1. [Instale o Ollama](https://ollama.com/download).
2. Com o Ollama em execução, rode `ollama pull moondream` no terminal.
   Se precisar iniciar o serviço manualmente para baixar o modelo, rode
   `ollama serve` em outro terminal.
3. Abra o Separador pelo endereço local e adicione uma imagem com grade.
   A aba **Células** abre e as legendas aparecem uma por vez. Com o
   Moondream, as legendas são geradas em inglês.

Quando o executável `ollama` está disponível ao servidor, o Separador o
inicia ao abrir uma aba e encerra o processo que iniciou após fechar a
última aba. Se o Ollama já estiver em execução, o Separador usa essa
instância. Você pode mudar o modelo em **Ajustes**.

## Sugerir respostas

Baixe também o modelo de comparação com `ollama pull qwen2.5:1.5b`.
Depois que a pergunta, a referência (quando houver) e as descrições
estiverem prontas, clique em **Sugerir respostas** em **Cabeçalho** ou
**Células**. A grade mostra o resultado e o motivo por recorte.
Revise as sugestões antes de clicar em **Marcar sugestões pendentes**.
Recortes sem legenda aparecem como **Incerta**.

## Uso e armazenamento

- Em **Cabeçalho**, veja o retângulo acima da grade, a transcrição do
  texto e, se houver uma imagem quadrada, o recorte e a descrição dela.
  Para a transcrição, instale o Tesseract com os idiomas português e
  inglês. Em Ubuntu ou Debian, use
  `sudo apt install tesseract-ocr tesseract-ocr-por tesseract-ocr-eng`.
  A descrição da imagem de referência usa o Ollama.
- Adicione imagens pelo botão, arraste arquivos ou cole uma captura
  com **Ctrl+V**.
- Em **Mapa**, veja a grade numerada. Em **Células**, abra os recortes e
  acompanhe as descrições. Em **Marcações**, filtre status e observações.
- Se a grade sair errada, ajuste **Branco a partir de** ou **Vão mínimo**
  em **Ajustes** e clique em **Reprocessar**.
- Para excluir uma imagem importada, clique em **Apagar** ao lado dela
  e confirme. O arquivo original continua na sua pasta.

O navegador guarda as imagens, marcações e legendas por endereço e por
perfil. Use o mesmo endereço e perfil para reencontrar o que salvou.
O processamento das imagens e das legendas ocorre no computador.

## Para o Claude na máquina do trabalho

Abra o repositório no Claude Code e peça: “Siga o roteiro abaixo para
fazer o Separador rodar nesta máquina. Valide o fluxo completo com uma
captura de teste que eu fornecer.”

1. Identifique o sistema operacional. Se o projeto já estiver clonado,
   confira `git status --short --branch` e atualize a branch `main` com
   `git pull --ff-only` quando ela estiver limpa. Se precisar clonar, use
   o endereço da seção de instalação acima.
2. Verifique o Python 3 e o Tesseract. Rode `tesseract --list-langs`
   e confira se aparecem `por` e `eng`. Se faltarem, instale os pacotes
   indicados acima ou os equivalentes do sistema.
3. Verifique o Ollama com `ollama list`. Se o serviço estiver parado,
   inicie-o com `ollama serve` em outro terminal. Baixe apenas os modelos
   ausentes com `ollama pull moondream` e `ollama pull qwen2.5:1.5b`.
   Espere cada download terminar e confirme os dois em `ollama list`.
4. Abra o app com `bash abrir_separador.sh` no Linux ou
   `py servidor_separador.py` no Windows. Use o endereço local exibido.
   Com a porta padrão, estes dois comandos devem devolver `"ok": true`.

   ```bash
   curl "http://127.0.0.1:8765/api/ollama/status?model=moondream"
   curl "http://127.0.0.1:8765/api/ollama/status?model=qwen2.5%3A1.5b"
   ```

5. Importe uma captura de teste com grade 3×3. Confira os nove recortes,
   a leitura do cabeçalho e as legendas. Clique em **Sugerir respostas**
   e verifique se a grade mostra os resultados para revisão. Informe
   quais etapas passaram e o erro exato de qualquer etapa pendente.

Os modelos são instalados em cada máquina. As imagens já importadas
ficam no perfil do navegador em que foram abertas.
