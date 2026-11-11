# Prompt de refatoração — Separador de Imagens

```text
Siga AGENTS.md e CLAUDE.md.

Melhore a qualidade do código do separador, preservando a detecção da grade,
recortes, numeração, geração do PDF, o envio do PDF ao DeepSeek, a gravação do
TXT em pdf/respostas e os dados antigos do navegador.

Use apenas Python 3 com biblioteca padrão e HTML/CSS/JavaScript local.
O único processo é servidor_separador.py: não crie vigias, agentes, tarefas
agendadas ou um segundo servidor. Não adicione serviços de IA, OCR, modelos ou
dependências além do envio já existente ao DeepSeek pela extensão do Chrome.
Não abra imagens privadas; use apenas dados sintéticos nos testes.

Mapeie os trechos relevantes, proponha mudanças pequenas e mostre o diff
antes de aplicar. Não altere comportamento fora do pedido.

Valide o Python com py -m py_compile servidor_separador.py, a sintaxe do
JavaScript e as funções afetadas com testes adequados à mudança. Confirme que
/api/status e /api/fluxo respondem e que as rotas /api/vigia/* recusam chamadas
sem token.
```
