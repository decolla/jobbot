import pytest
import yaml

from conftest import write_docx
from jobbot.profile import (
    ConfigError,
    check_setup,
    load_profile,
    load_settings,
    read_document_text,
)


def _edit_yaml(path, change):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _errors(root):
    return [i.message for i in check_setup(root) if i.level == "error"]


def _warnings(root):
    return [i.message for i in check_setup(root) if i.level == "warning"]


def test_profile_error_names_the_missing_field(project):
    _edit_yaml(project / "config/profile.yaml", lambda d: d["personal"].pop("email"))
    with pytest.raises(ConfigError, match=r"personal\.email"):
        load_profile(project)


def test_settings_default_to_dry_run_when_mode_is_omitted(project):
    _edit_yaml(project / "config/settings.yaml", lambda d: d.pop("mode"))
    assert load_settings(project).mode == "dry-run"


def test_settings_default_limit_is_15_when_omitted(project):
    _edit_yaml(project / "config/settings.yaml", lambda d: d.pop("daily_application_limit"))
    assert load_settings(project).daily_application_limit == 15


def test_settings_reject_unknown_mode(project):
    _edit_yaml(project / "config/settings.yaml", lambda d: d.update(mode="turbo"))
    with pytest.raises(ConfigError, match="mode"):
        load_settings(project)


@pytest.mark.parametrize("match", ["", "   "])
def test_saved_answer_with_empty_match_is_rejected(project, match):
    # Um trecho vazio casaria com qualquer pergunta do formulário.
    _edit_yaml(project / "config/profile.yaml", lambda d: d.update(saved_answers=[{"match": match, "answer": "Não"}]))
    with pytest.raises(ConfigError, match="match"):
        load_profile(project)


def test_settings_reject_misspelled_key(project):
    _edit_yaml(project / "config/settings.yaml", lambda d: d.update(daily_limit=5))
    with pytest.raises(ConfigError, match="daily_limit"):
        load_settings(project)


def test_complete_setup_has_no_errors(project):
    assert _errors(project) == []


def test_missing_profile_is_an_error(project):
    (project / "config/profile.yaml").unlink()
    assert any("profile.yaml" in m for m in _errors(project))


def test_missing_settings_is_an_error(project):
    (project / "config/settings.yaml").unlink()
    assert any("settings.yaml" in m for m in _errors(project))


def test_missing_resume_is_an_error(project):
    (project / "documents/curriculo.docx").unlink()
    assert any("curriculo.docx" in m for m in _errors(project))


def test_missing_extra_document_is_an_error(project):
    _edit_yaml(project / "config/profile.yaml", lambda d: d["documents"].update(extras=["documents/historico.pdf"]))
    assert any("historico.pdf" in m for m in _errors(project))


def test_example_profile_is_flagged(project):
    assert any("exemplo" in m for m in _warnings(project))


def test_real_profile_is_not_flagged_as_example(project):
    _edit_yaml(project / "config/profile.yaml", lambda d: d["personal"].update(email="pessoa@real.com"))
    assert not any("exemplo" in m for m in _warnings(project))
    assert load_profile(project).is_example is False


def test_read_docx_returns_paragraph_text(tmp_path):
    path = tmp_path / "cv.docx"
    write_docx(path, "Primeira linha", "Segunda linha")
    assert read_document_text(path) == "Primeira linha\nSegunda linha"


def test_read_pdf_returns_page_text(tmp_path):
    stream = b"BT /F1 12 Tf 20 700 Td (Monitor de Algoritmos) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    pdf += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % xref
    path = tmp_path / "cv.pdf"
    path.write_bytes(pdf)
    assert "Monitor de Algoritmos" in read_document_text(path)


def test_read_unsupported_document_raises(tmp_path):
    path = tmp_path / "cv.odt"
    path.write_text("x")
    with pytest.raises(ConfigError, match="cv.odt"):
        read_document_text(path)


def test_resume_for_english_job_uses_english_resume_when_set(project):
    _edit_yaml(project / "config/profile.yaml", lambda d: d["documents"].update(resume_en="documents/cv-en.pdf"))
    profile = load_profile(project)
    assert profile.documents.resume_for("en") == "documents/cv-en.pdf"
    assert profile.documents.resume_for("pt") == profile.documents.resume


def test_resume_for_english_job_falls_back_to_main_resume(project):
    profile = load_profile(project)
    assert profile.documents.resume_for("en") == profile.documents.resume


def test_missing_english_resume_is_an_error(project):
    _edit_yaml(project / "config/profile.yaml", lambda d: d["documents"].update(resume_en="documents/nao-existe.pdf"))
    assert any("nao-existe.pdf" in e for e in _errors(project))
