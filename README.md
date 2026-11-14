# jobbot

Agente pessoal de candidaturas a vagas de estágio e júnior, rodando na sua máquina.

Todo dia ele:

1. busca vagas em fontes públicas;
2. filtra por regras e pontua de 0 a 100 com o Claude (`claude -p`);
3. prepara a candidatura com o seu currículo e uma carta adaptada à vaga, e envia ou não conforme o modo;
4. acompanha os processos pelo seu e-mail e pelos portais;
5. cria pendências para o que só você pode fazer (testes, entrevistas, vídeos), com material de treino;
6. gera um relatório em `reports/AAAA-MM-DD.md` e, se você quiser, avisa pelo Telegram ou por e-mail.

## O que ele nunca faz

- Inventar fatos. Cartas e respostas usam só o que está em `config/profile.yaml` e `documents/`. Cada afirmação
  passa por uma checagem de evidência antes de ser usada.
- Resolver testes ou avaliações por você. Ele detecta, registra prazo e link, avisa e gera exercícios **de treino**.
- Enviar sem você decidir. O modo padrão é `dry-run`; trocar de modo é decisão sua.
- Ler, guardar ou digitar senhas. Login em portais é feito por você, no navegador. Campo de senha faz o
  preenchimento parar.
- Contornar CAPTCHA ou bloqueio anti-bot. Ele para e chama você.
- Responder pretensão salarial, dados sensíveis ou perguntas ambíguas sem você. Ele para e sinaliza.
- Candidatar duas vezes à mesma vaga. Envio com resultado incerto nunca é repetido sozinho.
- Coletar de sites cujos termos de uso proíbem. Essas fontes ficam desligadas.

## Instalação

Precisa de Python 3.11+, [uv](https://docs.astral.sh/uv/) e do [Claude Code](https://claude.com/claude-code)
instalado e logado (o comando `claude` precisa funcionar no terminal).

```bash
cd jobbot
uv sync
uv run playwright install chromium
```

## Configuração

1. **Perfil**: copie `config/profile.example.yaml` para `config/profile.yaml` e troque pelos seus dados.
   Tudo ali é tratado como verdade. Use `facts` para fatos verdadeiros que não estão no currículo.
   Enquanto o e-mail terminar em `@exemplo.com`, nenhum envio real acontece.
2. **Documentos**: coloque o currículo (PDF ou DOCX) em `documents/` e aponte `documents.resume` para ele.
   Se tiver um currículo em inglês, aponte `documents.resume_en`: as vagas escritas em inglês usam ele na
   pontuação, na carta e no anexo. Histórico, portfólio etc. vão em `documents.extras`. PDFs gerados em LaTeX
   funcionam: os acentos soltos que a extração de texto produz são corrigidos.
3. **Preferências**: `config/settings.yaml` tem o modo, o score mínimo, o limite diário, as fontes ligadas,
   o acompanhamento por e-mail, as notificações e a resposta automática.
4. **Segredos (opcional)**: copie `.env.example` para `.env`. Só é preciso para ler e-mail (IMAP), enviar
   e-mail (SMTP) ou usar o Telegram. Use uma *senha de app* do seu provedor, nunca a senha principal.

Confira tudo com:

```bash
uv run jobbot status
```

## Primeiro uso

```bash
uv run jobbot search     # busca vagas
uv run jobbot score      # filtra e pontua
uv run jobbot apply      # em dry-run: preenche sem enviar
uv run jobbot report     # relatório do dia
```

Em dry-run, cada candidatura fica em `data/applications/<id>/`:

- `pacote.md`: link, score, carta e respostas prontas;
- `carta.txt`: carta usada no formulário;
- `formulario.png`: o formulário preenchido, sem envio e sem anexos.

Quando o resultado parecer bom, passe para `approval`: `uv run jobbot apply --mode approval` mostra cada
candidatura e pergunta antes de enviar. Rodando agendado, sem terminal, elas ficam esperando
`jobbot approve <id>`. Use `auto` só quando confiar no que está sendo enviado.

| Modo | O que acontece |
|---|---|
| `dry-run` (padrão) | Prepara e preenche, nunca envia. Você pode enviar depois com `jobbot approve <id>`. |
| `approval` | Pede sua confirmação para cada envio. |
| `auto` | Envia sozinho as vagas com score acima de `min_score`, até `daily_application_limit` por dia. |

## Fontes

| Fonte | Padrão | Como |
|---|---|---|
| Greenhouse e Lever | ligadas | APIs públicas das páginas de carreira. Edite `boards` e `companies` em `settings.yaml`. |
| Programathor | ligada | Páginas públicas. A candidatura exige login, então fica para você enviar. |
| Gupy | desligada | Os termos de uso proíbem agregar ou copiar partes da plataforma. |
| Vagas.com | desligada | Os termos de uso proíbem reproduzir o conteúdo. |
| LinkedIn e Indeed | sem automação | Pelos alertas no seu e-mail, ou por `jobbot add`. |

Gupy e Vagas.com estão implementadas. Ligar ou não é decisão sua, depois de ler os termos.

**LinkedIn e Indeed sem automação**, de dois jeitos:

- **Alertas por e-mail**: crie alertas de vaga no LinkedIn e no Indeed e ligue `sources.email_alerts` (precisa de
  IMAP no `.env`). O jobbot lê só os e-mails desses remetentes e registra as vagas como "para você enviar".
- **`jobbot add <url>`**: abra a vaga, copie o texto da página (Ctrl+A, Ctrl+C) e rode o comando com o link.
  O jobbot pontua e prepara carta e respostas.

As vagas que só você pode enviar aparecem no relatório com link e pacote. Depois de enviar, rode
`jobbot done <id>`. Se o acompanhamento por e-mail estiver ligado, a confirmação da empresa já registra o envio.

## Quando a candidatura para

O relatório lista cada candidatura parada e o motivo:

- **CAPTCHA ou verificação anti-bot**: envie à mão pelo link e rode `jobbot done <id>`.
- **Login**: rode `jobbot login <url>`, entre na janela do navegador e feche. A sessão fica em
  `data/browser_profile/`. Depois rode `jobbot apply --retry-blocked`.
- **Pretensão salarial**: sempre para. Responda à mão.
- **Pergunta sensível obrigatória** (CPF, PcD, raça, orientação, consentimentos...): para. Se a pergunta for
  opcional, fica em branco. Para respostas que você decidiu dar sempre, use `saved_answers` no perfil:

  ```yaml
  saved_answers:
    - match: "pessoa com deficiência"   # trecho da pergunta
      answer: "Não"
  ```

- **Pergunta que seus documentos não respondem**: para. O pacote traz o que já dá para responder.

`jobbot reject <id>` descarta uma candidatura.

## Acompanhamento

`jobbot track` faz duas coisas:

- **Portais**: visita a página das vagas enviadas e registra as encerradas.
- **E-mail** (com `tracking.email: true` e IMAP no `.env`): lê a caixa de entrada só para leitura, sem apagar
  nem marcar como lido. Associa cada e-mail a uma candidatura pela empresa no remetente ou no texto. Na dúvida
  entre duas candidaturas, avisa em vez de adivinhar.

Só e-mails do domínio da empresa ou de plataformas de recrutamento conhecidas (Gupy, Greenhouse, Lever, LinkedIn,
Indeed e outras) registram etapas. Um remetente qualquer que só cita a empresa, como uma newsletter, um recrutador
com e-mail pessoal ou um golpe, não muda nada: o e-mail aparece no relatório para você conferir.

Etapas reconhecidas: em análise, teste, vídeo, entrevista, proposta e recusa. Um e-mail só vai ao Claude se citar
a empresa de uma candidatura e falar de seleção, e vai sem e-mails e telefones. A fatura de uma empresa em que você
também é cliente, por exemplo, não sai da máquina.

Teste, vídeo, entrevista e proposta viram **pendência** com prazo e link quando o e-mail traz. Para teste, vídeo
e entrevista, o jobbot gera material de treino em `reports/prep/`. Quando terminar, rode `jobbot resolve <id>`
com o número que aparece em `jobbot status`. Uma recusa fecha as pendências daquela candidatura.

**Resposta automática** (desligada por padrão; `auto_reply.enabled`):

- Responde só "ainda tem interesse?" e "qual sua disponibilidade?", com modelo fixo e a `availability` do perfil.
- Responde só a e-mails do domínio da própria empresa.
- Não responde mensagem longa, endereço no-reply ou qualquer mensagem que peça outra coisa (documento, link,
  salário, anexo...).
- Responde no máximo uma vez a mesma coisa por candidatura.
- Cada resposta enviada fica registrada.

## Relatório e notificações

`reports/AAAA-MM-DD.md` traz:

- resumo e métricas;
- suas pendências, com prazo (marcadas ATRASADA ou URGENTE);
- o que você precisa enviar ou aprovar;
- candidaturas paradas;
- novidades nos processos;
- enviadas hoje;
- vagas novas acima do score;
- erros do dia.

Para receber um resumo, ligue `notifications.telegram` ou `notifications.email` e preencha o `.env`. Para o
Telegram, crie um bot com o @BotFather e descubra seu chat id.

## Rotina diária e agendamento

`jobbot run-daily` faz busca, pontuação, candidatura no modo de `settings.yaml`, acompanhamento, relatório e
notificação. A falha de uma etapa vai para o relatório e não impede as seguintes.

Os arquivos de exemplo ficam em `src/jobbot/scheduler/`. Nada é agendado sem você ativar.

- **Windows**, no PowerShell, na pasta do projeto:

  ```powershell
  powershell -ExecutionPolicy Bypass -File src\jobbot\scheduler\register_windows_task.ps1 -Time 08:30
  ```

  Para remover, rode o mesmo comando com `-Remove`. A tarefa roda com você logado e, se o computador estiver
  desligado no horário, assim que possível. A saída vai para `data/run-daily.log`.
- **Linux (systemd)**: siga as instruções no topo de `jobbot.service` (usa `jobbot.timer`).
- **Linux ou macOS (cron)**: siga as instruções em `jobbot-daily.cron`.

## Comandos

| Comando | O que faz |
|---|---|
| `jobbot status` | Configuração, contagens e pendências |
| `jobbot search` | Busca vagas nas fontes ligadas |
| `jobbot score` | Filtra por regras e pontua com o Claude (até `max_scores_per_run` por vez) |
| `jobbot apply [--mode M] [--retry-blocked]` | Prepara e envia conforme o modo |
| `jobbot add <url>` | Registra uma vaga cujo texto você copiou |
| `jobbot approve <id>` / `reject <id>` | Envia ou descarta uma candidatura |
| `jobbot done <id>` | Registra uma candidatura que você enviou à mão |
| `jobbot login <url>` | Abre o navegador para você entrar em um portal |
| `jobbot track` | Acompanha e-mails e portais |
| `jobbot resolve <id>` | Fecha uma pendência |
| `jobbot report` | Gera o relatório do dia |
| `jobbot run-daily` | Tudo em sequência |

## Auditoria

Tudo fica em `data/jobbot.db` (SQLite). A tabela `events` registra cada ação: candidatura preparada, enviada,
parada, etapa detectada, resposta automática. Respostas sensíveis aparecem como `***`. A tabela `logs` guarda
erros e avisos. Para ver os envios, por exemplo:

```bash
sqlite3 data/jobbot.db "SELECT created_at, kind, payload_json FROM events WHERE kind LIKE 'application_%' ORDER BY id"
```

## Limitações conhecidas

- O envio real só foi testado contra formulários locais, nunca em um site real. Use `approval` nas primeiras
  vezes e confira as capturas em `data/applications/`.
- Muitos formulários reais pedem dados sensíveis obrigatórios e vão parar até você decidir. `saved_answers` reduz isso.
- Greenhouse e Lever têm CAPTCHA invisível. Se ele virar desafio visível, a candidatura para.
- O leitor de alertas do LinkedIn e do Indeed foi feito com e-mails de exemplo. Se um alerta real não render vagas,
  o relatório avisa, e o parser precisa de ajuste com um e-mail real.
- A classificação de e-mails por regras cobre frases comuns em português e inglês. O resto vai ao Claude.

## Desenvolvimento

```bash
uv run pytest                    # todos os testes (os de navegador usam o Chromium)
uv run pytest -m "not browser"   # sem navegador
```

Os testes não acessam a internet. Parsers usam fixtures em `tests/fixtures/`, e o envio real só acontece contra
formulários locais.
