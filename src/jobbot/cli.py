"""Interface de linha de comando do jobbot."""

from __future__ import annotations

import argparse
import sys

from jobbot import db, pipeline
from jobbot.profile import ConfigError, check_setup, load_profile, load_settings, project_root


def open_db(root):
    return db.connect(root / "data" / "jobbot.db")


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
    return parser


def main(argv: list[str] | None = None) -> int:
    # O console do Windows usa cp1252 por padrão e quebraria acentos e símbolos.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"[ERRO] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
