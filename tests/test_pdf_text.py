"""Texto de PDF gerado por LaTeX: acentos soltos e letras separadas pelo kerning."""

import pytest

from jobbot.profile import clean_pdf_text


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("constru¸ c˜ ao", "construção"),
        ("Uberlˆ andia", "Uberlândia"),
        ("s´ olida", "sólida"),
        ("Mar¸ co de 2024", "Março de 2024"),
        ("sistemas distribu ´ ıdos", "sistemas distribuídos"),
        ("bancos h ´ ıbridos", "bancos híbridos"),
        ("aplicados ` a automa¸ c˜ ao", "aplicados à automação"),
        ("EDUCAC ¸˜AO", "EDUCAÇÃO"),
        ("COMPETˆENCIAS", "COMPETÊNCIAS"),
        ("Inglˆ es (Fluente)", "Inglês (Fluente)"),
    ],
)
def test_loose_latex_accents_are_recombined(raw, expected):
    assert clean_pdf_text(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("F ull-stack", "Full-stack"), ("S´ eries T emporais", "Séries Temporais"), ("PEDRO V ARISE", "PEDRO VARISE")],
)
def test_letters_split_by_kerning_are_joined(raw, expected):
    assert clean_pdf_text(raw) == expected


def test_normal_text_is_unchanged():
    text = "Python, C#, .NET e SQL. Um projeto de 2024 - A equipe e o time."
    assert clean_pdf_text(text) == text
