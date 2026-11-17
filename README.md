# Robô → PDF → ChatGPT Web → robô (tudo no terminal)

Abra `abrir_separador.bat`. Um único programa Python:

1. abre o robô (Selenium) e captura a imagem assim que ela para de mudar;
2. gera o PDF numerado dentro do navegador do robô, sem abrir página nem interface;
3. avisa a extensão do Chrome na hora (long-poll, sem esperar alarme);
4. a extensão anexa o PDF numa aba nova do ChatGPT Web, espera a resposta completa e a devolve;
5. o programa salva o TXT, clica nos números, confirma e captura a próxima imagem.

Cada etapa aparece no terminal com horário e tempo gasto. Comandos: `1,5,9` responde
manualmente, `p` nova captura, `v` verificar, `s` para o robô, `Ctrl+C` encerra.

Instalação: veja `GUIA_INSTALACAO.txt` (execute `INSTALAR.bat` e carregue
`pdf/extensao_deepseek` em `chrome://extensions` uma única vez; depois ela se
atualiza sozinha).

## Arquivos

- `servidor_separador.py`: o programa inteiro (servidor, robô, geração do PDF e código da extensão).
- `pdf/extensao_deepseek/`: extensão gerada a partir do Python (o nome da pasta preserva a instalação).
- `pdf/.fluxo.json` e `pdf/respostas/`: tarefas e respostas, preservadas.
- `INSTALAR.bat`, `abrir_separador.bat`, `abrir_separador.sh`: instalação e atalhos.

## Verificação (somente dados sintéticos)

```text
py -m py_compile servidor_separador.py
py test_fluxo.py
node test_extensao.js
node test_chatgpt_web.js
```
