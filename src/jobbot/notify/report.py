"""Relatório diário em Markdown e o resumo curto que vai nas notificações."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobbot import db

STAGE_LABELS = {
    "screening": "Em análise", "test": "Teste", "video": "Vídeo", "interview": "Entrevista",
    "offer": "Proposta", "rejected": "Recusa", "job_closed": "Vaga encerrada",
}
URGENT_DAYS = 2
MAX_NEW_JOBS = 30
MAX_LOG_LINES = 20
MAX_SUMMARY_ITEMS = 10
MAX_SUMMARY_CHARS = 3500


@dataclass
class DailyData:
    day: date
    new_by_source: dict[str, int]
    new_approved: list[sqlite3.Row]
    submitted_today: list[sqlite3.Row]
    submitted_total: int
    stage_counts: dict[str, int]
    news: list[tuple[str, str, str]]  # (etapa, "título (empresa)", assunto)
    actions: list[sqlite3.Row]
    to_send: list[sqlite3.Row]
    to_approve: list[sqlite3.Row]
    old_previews: int
    blocked: list[sqlite3.Row]
    uncertain: list[sqlite3.Row]
    errors: list[sqlite3.Row]
    warnings: list[sqlite3.Row]


def _news(conn: sqlite3.Connection, day: str) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        """SELECT e.kind, e.payload_json, j.title, j.company FROM events e
           JOIN applications a ON a.id = e.application_id JOIN jobs j ON j.id = a.job_id
           WHERE e.kind IN ('stage_detected', 'stage_unverified', 'job_closed') AND substr(e.created_at, 1, 10) = ?
           ORDER BY e.id""",
        (day,),
    ).fetchall()
    news = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        stage = payload.get("stage", "job_closed") if row["kind"] != "job_closed" else "job_closed"
        label = STAGE_LABELS.get(stage, stage)
        if row["kind"] == "stage_unverified":
            # Remetente fora da empresa e das plataformas de recrutamento: nada foi registrado.
            label = f"{label}? confira o e-mail de {payload.get('from', '')}"
        news.append((label, f"{row['title']} ({row['company']})", payload.get("subject", "")))
    return news


def collect(conn: sqlite3.Connection, day: date, min_score: int) -> DailyData:
    iso = day.isoformat()
    previews = db.applications_by_status(conn, "dry_run")
    return DailyData(
        day=day,
        new_by_source=dict(conn.execute(
            "SELECT source, COUNT(*) FROM jobs WHERE substr(first_seen_at, 1, 10) = ? GROUP BY source ORDER BY source",
            (iso,),
        ).fetchall()),
        new_approved=conn.execute(
            """SELECT * FROM jobs WHERE substr(first_seen_at, 1, 10) = ? AND status = 'scored' AND score >= ?
               ORDER BY score DESC, id LIMIT ?""",
            (iso, min_score, MAX_NEW_JOBS),
        ).fetchall(),
        submitted_today=[a for a in db.applications_by_status(conn, "submitted") if (a["submitted_at"] or "")[:10] == iso],
        submitted_total=conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'submitted'").fetchone()[0],
        stage_counts=dict(conn.execute("SELECT stage, COUNT(DISTINCT application_id) FROM stages GROUP BY stage").fetchall()),
        news=_news(conn, iso),
        # Candidatura parada ou incerta já aparece na própria seção, com os comandos certos.
        actions=[i for i in db.open_action_items(conn) if i["kind"] not in ("form_blocked", "submit_uncertain")],
        to_send=db.applications_by_status(conn, "ready_manual"),
        to_approve=db.applications_by_status(conn, "pending_approval") + [a for a in previews if a["updated_at"][:10] == iso],
        old_previews=sum(1 for a in previews if a["updated_at"][:10] != iso),
        blocked=db.applications_by_status(conn, "blocked"),
        uncertain=db.applications_by_status(conn, "submit_uncertain"),
        errors=db.recent_logs(conn, "error", day),
        warnings=db.recent_logs(conn, "warning", day),
    )


def _link(root: Path, path: Path | str) -> str:
    """Caminho relativo à pasta reports/, para o link funcionar no visualizador de Markdown."""
    return Path(os.path.relpath(root / path, root / "reports")).as_posix()


def _due_label(due: str | None, day: date) -> str:
    if not due:
        return "sem prazo"
    days_left = (date.fromisoformat(due) - day).days
    if days_left < 0:
        return f"ATRASADA {due}"
    if days_left <= URGENT_DAYS:
        return f"URGENTE {due}"
    return due


def _pack(root: Path, app_id: int) -> str:
    pack = Path("data") / "applications" / str(app_id) / "pacote.md"
    return f" · [pacote]({_link(root, pack)})" if (root / pack).exists() else ""


def _evaluation(root: Path, job_id: int) -> str:
    path = Path("reports") / "vagas" / f"{job_id}.md"
    return f" · [avaliação]({_link(root, path)})" if (root / path).exists() else ""


def _section(title: str, lines: list[str], empty: str = "Nenhuma.") -> list[str]:
    return [f"## {title}", "", *(lines or [empty]), ""]


def render_report(data: DailyData, root: Path) -> str:
    total_new = sum(data.new_by_source.values())
    by_source = ", ".join(f"{source}: {count}" for source, count in data.new_by_source.items())
    stages = " | ".join(f"{STAGE_LABELS[s]}: {data.stage_counts.get(s, 0)}"
                        for s in ("screening", "test", "video", "interview", "offer", "rejected"))
    summary = [
        f"- Vagas novas hoje: {total_new}" + (f" ({by_source})" if by_source else ""),
        f"- Candidaturas enviadas: {len(data.submitted_today)} hoje, {data.submitted_total} no total",
        f"- Processos: {stages}",
        f"- Pendências suas: {len(data.actions)} | para você enviar: {len(data.to_send)} | "
        f"aguardando aprovação: {len(data.to_approve)} | paradas: {len(data.blocked) + len(data.uncertain)}",
    ]
    actions = []
    for item in data.actions:
        line = f"- **{_due_label(item['due_date'], data.day)}** {item['title']} ({item['company']})"
        if item["link"]:
            line += f" · {item['link']}"
        if item["prep_path"]:
            line += f" · [material de preparação]({_link(root, item['prep_path'])})"
        actions.append(line + f" · feito: `jobbot resolve {item['id']}`")
    to_send = [
        f"- {a['title']} ({a['company']}), score {a['score']}: {a['url']}{_pack(root, a['id'])}"
        f" · depois de enviar: `jobbot done {a['id']}`"
        for a in data.to_send
    ]
    to_approve = [
        f"- {a['title']} ({a['company']}), score {a['score']}{' (prévia em dry-run)' if a['status'] == 'dry_run' else ''}:"
        f" {a['url']} · enviar: `jobbot approve {a['id']}` · descartar: `jobbot reject {a['id']}`"
        for a in data.to_approve
    ]
    if data.old_previews:
        to_approve.append(f"- {data.old_previews} prévia(s) de dias anteriores continuam disponíveis para `jobbot approve`.")
    blocked = [f"- {a['title']} ({a['company']}): {a['reason']} · {a['url']}{_pack(root, a['id'])}" for a in data.blocked]
    if data.blocked:
        blocked.append("- Depois de resolver, rode `jobbot apply --retry-blocked`, ou envie à mão e rode `jobbot done <id>`.")
    blocked += [
        f"- Envio incerto: {a['title']} ({a['company']}): confira pelo link {a['url']} se a candidatura chegou;"
        f" se sim, `jobbot done {a['id']}`, se não, envie à mão"
        for a in data.uncertain
    ]
    news = [f"- {label}: {job}" + (f': "{subject}"' if subject else "") for label, job, subject in data.news]
    submitted = [f"- {a['title']} ({a['company']}): {a['url']}" for a in data.submitted_today]
    new_jobs = [f"- {j['score']} · {j['title']} ({j['company']}): {j['url']}{_evaluation(root, j['id'])}"
                for j in data.new_approved]
    logs = [f"- [erro] {r['component']}: {r['message']}" for r in data.errors]
    logs += [f"- [aviso] {r['component']}: {r['message']}" for r in data.warnings]
    if len(logs) > MAX_LOG_LINES:
        logs = logs[:MAX_LOG_LINES] + [f"- e mais {len(logs) - MAX_LOG_LINES} (veja a tabela logs do banco)"]
    lines = [f"# jobbot: {data.day.isoformat()}", ""]
    lines += _section("Resumo", summary)
    lines += _section("Pendências suas", actions, "Nada pendente.")
    lines += _section("Para você enviar", to_send)
    lines += _section("Aguardando sua aprovação", to_approve)
    lines += _section("Candidaturas paradas", blocked)
    lines += _section("Novidades nos processos", news)
    lines += _section("Enviadas hoje", submitted)
    lines += _section("Vagas novas acima do score mínimo", new_jobs)
    lines += _section("Erros e avisos", logs, "Nenhum.")
    return "\n".join(lines)


def render_summary(data: DailyData, report_path: Path) -> str:
    lines = [
        f"jobbot: {data.day.isoformat()}",
        f"Vagas novas: {sum(data.new_by_source.values())}",
        f"Enviadas hoje: {len(data.submitted_today)}",
        f"Para você enviar: {len(data.to_send)}",
        f"Aguardando aprovação: {len(data.to_approve)}",
        f"Paradas: {len(data.blocked) + len(data.uncertain)}",
    ]
    if data.actions:
        lines += ["", "Pendências:"]
        lines += [f"- [{_due_label(i['due_date'], data.day)}] {i['title']} ({i['company']})"
                  for i in data.actions[:MAX_SUMMARY_ITEMS]]
    if data.news:
        lines += ["", "Novidades:"]
        lines += [f"- {label}: {job}" for label, job, _ in data.news[:MAX_SUMMARY_ITEMS]]
    if data.errors:
        lines += ["", f"Erros hoje: {len(data.errors)} (veja o relatório)"]
    lines += ["", f"Relatório: {report_path}"]
    return "\n".join(lines)[:MAX_SUMMARY_CHARS]


def write_report(conn: sqlite3.Connection, root: Path, day: date, min_score: int) -> tuple[Path, DailyData]:
    data = collect(conn, day, min_score)
    path = root / "reports" / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(data, root) + "\n", encoding="utf-8")
    return path, data
