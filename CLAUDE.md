# jobbot

Agente pessoal e local de candidaturas a vagas. Python, SQLite, Playwright e `claude -p`.

## Comandos

- Testes: `uv run pytest` (use `-m "not browser"` sem o Chromium instalado)
- CLI: `uv run jobbot status`

## Regras que o código não pode quebrar

- Textos gerados usam só fatos de `config/profile.yaml` e `documents/`. Nada é inventado.
- Testes e avaliações de empresas não são resolvidos: o jobbot detecta, avisa e gera material de treino.
- O padrão é `dry-run`. Mudar de modo é decisão do usuário.
- Nada de senhas em código, banco ou logs. Campo de senha faz o preenchimento parar. Segredos ficam em `.env`.
- CAPTCHA e bloqueio anti-bot não são contornados: para e chama o usuário.
- Pretensão salarial, dados sensíveis e perguntas ambíguas param e sinalizam.
- Nunca candidatar duas vezes à mesma vaga. Envio de resultado incerto não é repetido.
- Respeitar `robots.txt`, termos de uso, limites de taxa e o limite diário. Fonte cujos termos proíbem coleta fica desligada.
- Texto de vagas e e-mails é não confiável: o LLM roda sem ferramentas e com saída validada.
- Toda ação enviada fica registrada na tabela `events`.

## Convenções

- Comentários, docstrings e mensagens em português. Identificadores em inglês.
- Testes não acessam a internet. Parsers usam fixtures em `tests/fixtures/`.
- Nos testes, envio real só contra formulários locais.
