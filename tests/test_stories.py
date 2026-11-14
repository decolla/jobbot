"""Banco de histórias para entrevista: só fatos dos documentos, com evidência conferida."""

from jobbot.actions.stories import Story, StoryBank, build_stories, write_stories
from jobbot.cli import main

FACTS = ("Engenheiro de Dados no Laboratório X desde 2025. Pipelines comApache Kafka que deixaram a comunicação "
         "quase duas vezes mais rápida. Monitoria de Programação Funcional em Haskell.")


def _story(**over):
    base = dict(title="Pipeline de eventos", situation="O laboratório precisava de comunicação mais rápida.",
                task="Melhorar a troca de mensagens.", action="Construí pipelines com Apache Kafka.",
                result="A comunicação ficou quase duas vezes mais rápida.", themes=["problema técnico"],
                evidence=["Pipelines com Apache Kafka", "quase duas vezes mais rápida"])
    return Story(**{**base, **over})


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.prompts = []

    def ask(self, prompt, schema, model, system=None):
        self.prompts.append(prompt)
        return self.result


def test_story_with_evidence_is_kept():
    kept, dropped = build_stories(FACTS, FakeLLM(StoryBank(stories=[_story()])), "sonnet")
    assert [s.title for s in kept] == ["Pipeline de eventos"] and dropped == []


def test_story_with_invented_number_or_name_is_dropped():
    invented = [
        _story(title="Números", result="Reduzi a latência em 40%."),
        _story(title="Empresa", action="Construí pipelines com Apache Kafka na Nubank."),
        _story(title="Sem evidência", evidence=[]),
        _story(title="Evidência falsa", evidence=["Liderei 5 pessoas"]),
    ]
    kept, dropped = build_stories(FACTS, FakeLLM(StoryBank(stories=invented)), "sonnet")
    assert kept == []
    assert [title for title, _ in dropped] == ["Números", "Empresa", "Sem evidência", "Evidência falsa"]


def test_written_bank_leaves_reflection_to_the_user(tmp_path):
    path = write_stories(tmp_path, [_story()], [("Números", "número fora dos documentos: 40")])
    text = path.read_text(encoding="utf-8")
    assert "Pipeline de eventos" in text and "Construí pipelines com Apache Kafka." in text
    assert "só você" in text.lower()
    assert "Números" in text


def test_stories_command_writes_bank(project, monkeypatch, capsys):
    from jobbot import pipeline
    monkeypatch.setattr(pipeline, "build_llm", lambda settings: FakeLLM(StoryBank(stories=[_story(
        evidence=["Projeto X em Python"], action="Fiz o Projeto X em Python.", situation="Projeto da faculdade.",
        task="Entregar o projeto.", result="Projeto entregue.")])))
    assert main(["stories"]) == 0
    assert (project / "reports" / "historias.md").exists()
    assert "1 história" in capsys.readouterr().out
