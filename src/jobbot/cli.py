"""Interface de linha de comando do jobbot."""

from __future__ import annotations

import argparse
import sys

from jobbot.profile import ConfigError, check_setup, load_settings, project_root


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
    return parser


def main(argv: list[str] | None = None) -> int:
    # O console do Windows usa cp1252 por padrão e quebraria acentos e símbolos.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
