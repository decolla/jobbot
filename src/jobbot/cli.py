"""Interface de linha de comando do jobbot."""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date

from jobbot import db, pipeline
from jobbot.actions.followup import plan_followups
from jobbot.actions.stories import build_stories, write_stories
from jobbot.apply.text_tailor import build_facts
from jobbot.apply import manual
from jobbot.apply.base import Applier, ApplySummary, mark_done, recover_interrupted, reject_application
from jobbot.apply.form_filler import login_interactively
from jobbot.llm import LLMError
from jobbot.notify import notify
from jobbot.notify.email import send_email
from jobbot.notify.report import render_summary, write_report
from jobbot.profile import (
    ConfigError,
    check_setup,
    load_env,
    load_profile,
    load_settings,
    project_root,
    read_document_text,
)
from jobbot.tracking.mailbox import Mailbox, MailboxError
from jobbot.tracking.status_checker import check_open_jobs, run_tracking


def open_db(root):
    return db.connect(root / "data" / "jobbot.db")


def confirm_on_terminal(preview: str) -> bool:
    print(preview)
    return input("Enviar esta candidatura? [s/N] ").strip().lower() in ("s", "sim")


def _build_applier(conn, root, settings) -> Applier:
    confirm = confirm_on_terminal if sys.stdin.isatty() else None
    return Applier(conn, settings, load_profile(root), root, pipeline.build_llm(settings), confirm=confirm)


def _print_apply_summary(mode: str, summary: ApplySummary) -> None:
    print(
        f"Modo: {mode} | enviadas: {summary.submitted} | dry-run: {summary.dry_run} | "
        f"aguardando aprovação: {summary.pending_approval} | para você enviar: {summary.manual} | "
        f"paradas: {summary.blocked} | envio incerto: {summary.uncertain} | encerradas: {summary.closed} | "
        f"fora do limite: {summary.skipped_by_limit}"
    )


def cmd_search(args: argparse.Namespace) -> int:
    root = project_root()
    settings = load_settings(root)
    sources = pipeline.build_enabled_sources(settings)
    conn = open_db(root)
    try:
        summary = pipeline.run_search(conn, sources)
    finally:
        conn.close()
    for name, (found, new) in summary.per_source.items():
        print(f"{name}: {found} encontradas, {new} nova(s)")
    for name, reason in summary.failures.items():
        print(f"[FALHA] {name}: {reason}")
    print(f"Total de vagas novas: {summary.new_jobs}")
    return 1 if sources and len(summary.failures) == len(sources) else 0


def cmd_score(args: argparse.Namespace) -> int:
    root = project_root()
    settings = load_settings(root)
    profile = load_profile(root)
    conn = open_db(root)
    try:
        summary = pipeline.run_score(
            conn, settings, profile, root, pipeline.build_llm(settings), pipeline.build_enabled_sources(settings)
        )
    finally:
        conn.close()
    print(
        f"{summary.approved} aprovada(s), {summary.low_score} abaixo do score mínimo, "
        f"{summary.discarded} descartada(s) pelas regras, {summary.errors} erro(s)"
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    root = project_root()
    settings = load_settings(root)
    mode = args.mode or settings.mode
    conn = open_db(root)
    try:
        recovered = recover_interrupted(conn)
        if recovered:
            print(f"[AVISO] {recovered} envio(s) interrompido(s) viraram pendência para você conferir.")
        summary = _build_applier(conn, root, settings).run(mode, retry_blocked=args.retry_blocked)
    finally:
        conn.close()
    _print_apply_summary(mode, summary)
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    root = project_root()
    settings = load_settings(root)
    conn = open_db(root)
    try:
        status = _build_applier(conn, root, settings).approve(args.id)
    finally:
        conn.close()
    print(f"Candidatura {args.id}: {status}")
    return 0 if status == "submitted" else 1


def cmd_reject(args: argparse.Namespace) -> int:
    conn = open_db(project_root())
    try:
        reject_application(conn, args.id)
    finally:
        conn.close()
    print(f"Candidatura {args.id} descartada.")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    conn = open_db(project_root())
    try:
        mark_done(conn, args.id)
    finally:
        conn.close()
    print(f"Candidatura {args.id} registrada como enviada.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    root = project_root()
    settings = load_settings(root)
    profile = load_profile(root)
    conn = open_db(root)
    try:
        result = manual.add_manual_job(conn, settings, profile, root, pipeline.build_llm(settings), args.url)
    finally:
        conn.close()
    if not result.created:
        print(f"Esta vaga já está registrada (vaga {result.job_id}).")
        return 0
    print(f"Score: {result.score} ({result.reason})")
    print(f"Material pronto em: {result.pack_path}")
    return 0


def _email_sender(env: dict[str, str]):
    def send(to: str, subject: str, body: str, in_reply_to: str | None = None) -> None:
        send_email(env, to, subject, body, in_reply_to=in_reply_to)

    return send


def cmd_track(args: argparse.Namespace) -> int:
    root = project_root()
    settings = load_settings(root)
    profile = load_profile(root)
    conn = open_db(root)
    failed = False
    try:
        if settings.tracking.email:
            env = load_env(root)
            send = _email_sender(env) if settings.auto_reply.enabled else None
            try:
                summary = run_tracking(conn, settings, profile, root, Mailbox.from_env(env),
                                       pipeline.build_llm(settings), send=send)
            except (MailboxError, ConfigError) as exc:
                failed = True
                db.log(conn, "error", "track", str(exc))
                print(f"[FALHA] e-mail: {exc}")
            else:
                print(
                    f"E-mails novos: {summary.messages} | etapas registradas: {summary.stage_changes} | "
                    f"pendências criadas: {summary.actions} | envios confirmados: {summary.confirmed} | "
                    f"respostas automáticas: {summary.replies} | para conferir: {summary.ambiguous + summary.unverified} | "
                    f"erros: {summary.errors}"
                )
        else:
            print("Acompanhamento por e-mail desligado (tracking.email em settings.yaml).")
        closed = check_open_jobs(conn, pipeline.build_http(settings))
        followups = plan_followups(conn, root, profile, settings, date.today())
    finally:
        conn.close()
    print(f"Portais: {closed} vaga(s) encerrada(s).")
    print(f"Follow-ups: {followups.created} rascunho(s) novo(s) | marcadas sem resposta: {followups.cold}")
    return 1 if failed else 0


def cmd_resolve(args: argparse.Namespace) -> int:
    conn = open_db(project_root())
    try:
        done = db.complete_action_item(conn, args.id)
        if done:
            db.log_event(conn, "action_resolved", payload={"action_item": args.id})
    finally:
        conn.close()
    if not done:
        print(f"[ERRO] pendência {args.id} não existe ou já foi resolvida", file=sys.stderr)
        return 1
    print(f"Pendência {args.id} resolvida.")
    return 0


def _write_report(root, settings) -> tuple:
    conn = open_db(root)
    try:
        path, data = write_report(conn, root, date.today(), settings.min_score)
    finally:
        conn.close()
    return path, render_summary(data, path)


def cmd_stories(args: argparse.Namespace) -> int:
    root = project_root()
    settings = load_settings(root)
    profile = load_profile(root)
    resume_text = read_document_text(root / profile.documents.resume)
    # Os dois currículos são documentos seus: um fato de qualquer um deles vale.
    others = [profile.documents.resume_en, *profile.documents.extras] if profile.documents.resume_en else profile.documents.extras
    extras = [read_document_text(root / path) for path in others]
    try:
        kept, dropped = build_stories(build_facts(profile, resume_text, extras), pipeline.build_llm(settings),
                                      settings.llm.writing_model)
    except LLMError as exc:
        print(f"[FALHA] Claude: {exc}")
        return 1
    path = write_stories(root, kept, dropped)
    print(f"{len(kept)} história(s) em {path}" + (f" | {len(dropped)} descartada(s) pela conferência" if dropped else ""))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = project_root()
    path, summary = _write_report(root, load_settings(root))
    print(summary)
    return 0


DAILY_STEPS = (("busca", cmd_search), ("pontuação", cmd_score), ("candidatura", cmd_apply), ("acompanhamento", cmd_track))


def cmd_run_daily(args: argparse.Namespace) -> int:
    """Roda tudo em sequência. A falha de uma etapa fica no relatório e não impede as seguintes."""
    root = project_root()
    settings = load_settings(root)
    failures = []
    for name, step in DAILY_STEPS:
        print(f"== {name} ==", flush=True)
        try:
            if step(argparse.Namespace(mode=None, retry_blocked=False)) != 0:
                failures.append(name)
        except Exception as exc:  # execução sem ninguém olhando: registra e segue para a próxima etapa
            failures.append(name)
            print(f"[FALHA] {name}: {exc}", file=sys.stderr)
            traceback.print_exc()
            conn = open_db(root)
            try:
                db.log(conn, "error", "run-daily", f"{name}: {type(exc).__name__}: {exc}")
            finally:
                conn.close()
    print("== relatório ==", flush=True)
    path, summary = _write_report(root, settings)
    print(summary)
    for error in notify(settings, load_env(root), f"jobbot: relatório de {date.today().isoformat()}", summary):
        print(f"[AVISO] notificação: {error}")
    return 1 if failures else 0


def cmd_login(args: argparse.Namespace) -> int:
    print("Faça login na janela do navegador e feche-a ao terminar. O jobbot não vê nem guarda sua senha.")
    login_interactively(project_root() / "data" / "browser_profile", args.url)
    return 0


def _print_counts(title: str, rows) -> None:
    counts = ", ".join(f"{row[0]}: {row[1]}" for row in rows)
    print(f"{title}: {counts or 'nenhuma'}")


def cmd_status(args: argparse.Namespace) -> int:
    root = project_root()
    issues = check_setup(root)
    try:
        settings = load_settings(root)
    except ConfigError:
        settings = None
    if settings:
        print(f"Modo: {settings.mode} | Score mínimo: {settings.min_score} | Limite diário: {settings.daily_application_limit}")
        on = [name for name, src in settings.sources.items() if src.enabled]
        off = [name for name, src in settings.sources.items() if not src.enabled]
        print(f"Fontes ligadas: {', '.join(on) or 'nenhuma'}")
        print(f"Fontes desligadas: {', '.join(off) or 'nenhuma'}")
    conn = open_db(root)
    try:
        _print_counts("Vagas", conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        _print_counts("Candidaturas", conn.execute("SELECT status, COUNT(*) FROM applications GROUP BY status").fetchall())
        actions = db.open_action_items(conn)
        if actions:
            print("Pendências:")
        for item in actions:
            line = f"  - #{item['id']} [{item['due_date'] or 'sem prazo'}] {item['title']} ({item['company']}) {item['link'] or ''}"
            print(line.rstrip())
    finally:
        conn.close()
    labels = {"error": "ERRO", "warning": "AVISO"}
    for issue in issues:
        print(f"[{labels[issue.level]}] {issue.message}")
    if not issues:
        print("Configuração OK.")
    return 1 if any(i.level == "error" for i in issues) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobbot", description="Agente pessoal de candidaturas a vagas")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="mostra configuração e situação atual").set_defaults(func=cmd_status)
    commands.add_parser("search", help="busca vagas nas fontes ligadas").set_defaults(func=cmd_search)
    commands.add_parser("score", help="filtra e pontua as vagas novas").set_defaults(func=cmd_score)

    apply_parser = commands.add_parser("apply", help="prepara e envia candidaturas conforme o modo")
    apply_parser.add_argument("--mode", choices=["dry-run", "approval", "auto"], help="sobrepõe o modo de settings.yaml")
    apply_parser.add_argument("--retry-blocked", action="store_true", help="tenta de novo as candidaturas paradas")
    apply_parser.set_defaults(func=cmd_apply)

    add_parser = commands.add_parser("add", help="registra uma vaga cujo texto você copiou (LinkedIn, Indeed...)")
    add_parser.add_argument("url")
    add_parser.set_defaults(func=cmd_add)

    commands.add_parser("track", help="acompanha e-mails do processo e vagas encerradas").set_defaults(func=cmd_track)
    commands.add_parser("report", help="gera o relatório do dia em reports/").set_defaults(func=cmd_report)
    commands.add_parser("stories", help="gera suas histórias para entrevista a partir dos documentos").set_defaults(
        func=cmd_stories)
    commands.add_parser("run-daily", help="busca, pontua, candidata, acompanha e gera o relatório").set_defaults(
        func=cmd_run_daily
    )

    for name, func, description in (
        ("approve", cmd_approve, "aprova e envia uma candidatura"),
        ("reject", cmd_reject, "descarta uma candidatura"),
        ("done", cmd_done, "registra uma candidatura que você enviou à mão"),
        ("resolve", cmd_resolve, "marca uma pendência (teste, entrevista...) como resolvida"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("id", type=int)
        command.set_defaults(func=func)

    login_parser = commands.add_parser("login", help="abre o navegador para você entrar em um portal")
    login_parser.add_argument("url")
    login_parser.set_defaults(func=cmd_login)
    return parser


def main(argv: list[str] | None = None) -> int:
    # O console do Windows usa cp1252 por padrão e quebraria acentos e símbolos.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, LLMError) as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
