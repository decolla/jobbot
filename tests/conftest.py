import shutil
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def write_docx(path: Path, *paragraphs: str) -> None:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    """Projeto completo em pasta temporária, com os arquivos de exemplo do repositório."""
    (tmp_path / "config").mkdir()
    (tmp_path / "documents").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "reports").mkdir()
    shutil.copy(REPO / "config" / "settings.yaml", tmp_path / "config" / "settings.yaml")
    text = (REPO / "config" / "profile.example.yaml").read_text(encoding="utf-8")
    text = text.replace("documents/curriculo.pdf", "documents/curriculo.docx")
    (tmp_path / "config" / "profile.yaml").write_text(text, encoding="utf-8")
    write_docx(tmp_path / "documents" / "curriculo.docx", "Projeto X em Python", "Monitor de Algoritmos")
    monkeypatch.setenv("JOBBOT_HOME", str(tmp_path))
    return tmp_path


def fake_cv_answer(schema):
    """Resposta do Claude para o currículo adaptado, com o texto do currículo de teste (curriculo.docx)."""
    from jobbot.apply.cv_builder import CVEntry, CVPlan, CVSection, StructuredCV

    if schema is CVPlan:
        return CVPlan()
    if schema is StructuredCV:
        return StructuredCV(sections=[CVSection(heading="Monitor de Algoritmos", entries=[CVEntry(title="Projeto X em Python")])])
    return None
