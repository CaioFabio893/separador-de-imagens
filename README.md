# Print da tela → PDF → API da OpenAI → cliques (tudo no terminal)

Abra `abrir_separador.bat`. Um único programa Python, só para Windows:

1. tira um print da tela a cada 5 s (só em memória) e procura uma grade 3x3;
2. ao achar, gera o PDF numerado num Chrome invisível, sem abrir página nem interface;
3. envia a página numerada à API da OpenAI (`--modelo`, padrão `gpt-4.1`);
4. salva a resposta em TXT, clica nos números com o mouse do Windows, confirma e volta a vigiar.

A chave da OpenAI é obrigatória: coloque-a em `chave_openai.txt` (fora do git) ou na
variável `OPENAI_API_KEY`. Cada envio mostra no terminal os tokens gastos.

**Vigilância.** A espera é indefinida até achar uma grade ou receber `s`/`Ctrl+C`. A grade só
é aceita depois de aparecer igual em duas prints seguidas (1 s), e após cada resposta o
programa espera 2 s antes da primeira print. Se o mesmo desafio continuar na tela por 15 s
depois da resposta, ele responde de novo. Se a tela inteira não revelar a grade, o programa
procura as 9 fotos do desafio (blocos quadrados iguais em 3x3) e depois dentro de cada área
branca, então página com outros conteúdos, outro monitor ou fundo escurecido não atrapalham.
Enquanto não acha, o terminal mostra `Vigiando a tela: ...` com o motivo (só medidas). Com
mais de uma grade 3x3 na tela, usa a de fotos maiores.

**Cliques.** A resposta move o **mouse real do Windows**. O botão de confirmar é achado na
própria print (o bloco colorido logo abaixo da grade, à direita) e procurado de novo depois
de marcar as imagens; se não achar, usa a posição pela proporção do desafio. Uma grade 3×3
de outro aplicativo também pode acionar o fluxo, e outra janela que ocupar essas posições
antes da resposta pode receber os cliques.

**Produção (aberto por muito tempo).** Se o Chrome invisível cair, o programa o reabre
sozinho e continua vigiando; sem desafio na tela, ele é renovado a cada 2 h. Avisos que pedem
`p` ou números voltam a vigiar sozinhos após 30 s sem resposta, e um erro inesperado (mouse,
Chrome) não encerra o programa: ele espera 10 s e recomeça.

Comandos: `1,5,9` responde manualmente, `p` nova captura, `v` verificar, `s` ou `Ctrl+C` sai.

Instalação: veja `GUIA_INSTALACAO.txt` (execute `INSTALAR.bat` e crie o `chave_openai.txt`).

## Arquivos

- `servidor_separador.py`: o programa inteiro (print, detecção, PDF, API, cliques).
- `pdf/.fluxo.json` e `pdf/respostas/`: tarefas e respostas, preservadas.
- `INSTALAR.bat`, `abrir_separador.bat`, `abrir_separador.sh`: instalação e atalhos.

## Verificação (somente dados sintéticos)

```text
py -m py_compile servidor_separador.py
py test_fluxo.py
```
