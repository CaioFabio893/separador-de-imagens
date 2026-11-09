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

## Uso e armazenamento

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
