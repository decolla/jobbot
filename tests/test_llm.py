import json
import subprocess

import pytest
from pydantic import BaseModel, Field

from jobbot.llm import ClaudeClient, LLMError


class Answer(BaseModel):
    score: int = Field(ge=0, le=100)


class FakeProcess:
    """Substitui só o subprocess: captura o comando real e devolve saídas na ordem pedida."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def _ok(structured, **extra):
    stdout = json.dumps({"is_error": False, "structured_output": structured, "result": "", **extra})
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def _client(*outputs):
    fake = FakeProcess(*outputs)
    return ClaudeClient(timeout=10, run=fake), fake


def test_returns_validated_structured_output():
    client, _ = _client(_ok({"score": 42}))
    assert client.ask("p", Answer, model="haiku").score == 42


def test_disables_all_tools_and_session_persistence():
    client, fake = _client(_ok({"score": 1}))
    client.ask("p", Answer, model="haiku")
    cmd = fake.calls[0][0]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--model") + 1] == "haiku"


def test_prompt_goes_through_stdin_and_never_in_arguments():
    client, fake = _client(_ok({"score": 1}))
    client.ask("texto da vaga", Answer, model="haiku")
    cmd, kwargs = fake.calls[0]
    assert kwargs["input"] == "texto da vaga"
    assert not any("texto da vaga" in part for part in cmd)


def test_sends_schema_generated_from_model():
    client, fake = _client(_ok({"score": 1}))
    client.ask("p", Answer, model="haiku")
    cmd = fake.calls[0][0]
    schema = json.loads(cmd[cmd.index("--json-schema") + 1])
    assert schema["properties"]["score"]["maximum"] == 100


def test_retries_once_when_output_fails_validation():
    client, fake = _client(_ok({"score": 999}), _ok({"score": 50}))
    assert client.ask("p", Answer, model="haiku").score == 50
    assert len(fake.calls) == 2


def test_raises_after_second_invalid_output():
    client, fake = _client(_ok({"score": 999}), _ok({"score": 998}))
    with pytest.raises(LLMError):
        client.ask("p", Answer, model="haiku")
    assert len(fake.calls) == 2


def test_error_result_raises_without_retry():
    stdout = json.dumps({"is_error": True, "result": "limite atingido"})
    client, fake = _client(subprocess.CompletedProcess([], 0, stdout=stdout, stderr=""))
    with pytest.raises(LLMError, match="limite atingido"):
        client.ask("p", Answer, model="haiku")
    assert len(fake.calls) == 1


def test_nonzero_exit_raises():
    client, _ = _client(subprocess.CompletedProcess([], 1, stdout="", stderr="falhou"))
    with pytest.raises(LLMError, match="falhou"):
        client.ask("p", Answer, model="haiku")


def test_timeout_raises_llm_error():
    client, _ = _client(subprocess.TimeoutExpired("claude", 10))
    with pytest.raises(LLMError):
        client.ask("p", Answer, model="haiku")


def test_missing_binary_raises_llm_error():
    client, _ = _client(FileNotFoundError("claude"))
    with pytest.raises(LLMError):
        client.ask("p", Answer, model="haiku")
