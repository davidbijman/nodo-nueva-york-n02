"""Normalización y control de calidad del corpus de afirmaciones Sonantia."""

from __future__ import annotations

import re

TERMINAL_PUNCTUATION = (".", "!", "?")

_SYSTEMATIC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\besta evolución permite que\s+", re.IGNORECASE),
        "gracias a esta evolución, ",
    ),
    (
        re.compile(r"^Todo favorece que\s+", re.IGNORECASE),
        "Las condiciones actuales impulsan una evolución favorable: ",
    ),
)

_EXACT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "para convertir ideas en proyectos viables "
        "genera proyectos con posibilidades reales de éxito",
        "para convertir ideas en proyectos viables abre posibilidades reales de éxito",
    ),
    (
        "proyectos viables generan proyectos",
        "proyectos viables generan iniciativas",
    ),
    (
        "reservar capital para oportunidades favorables para actuar con rapidez "
        "cuando aparece una buena inversión",
        "reservar capital para aprovechar oportunidades favorables y actuar con "
        "rapidez cuando aparece una buena inversión",
    ),
    (
        "crear fondos para metas familiares importantes para financiar educación, "
        "viajes y mejoras del hogar",
        "crear fondos destinados a metas familiares importantes y financiar "
        "educación, viajes y mejoras del hogar",
    ),
    (
        "La combinación de su integridad y su capacidad para priorizar permite a "
        "{{NOMBRE}} alcanzar una reputación de integridad y cumplimiento.",
        "La combinación de su integridad y su capacidad para priorizar permite a "
        "{{NOMBRE}} consolidar una reputación de confiabilidad y cumplimiento.",
    ),
)

_KNOWN_ISSUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "subjuntivo-permite",
        re.compile(r"\besta evolución permite que\b", re.IGNORECASE),
    ),
    (
        "subjuntivo-favorece",
        re.compile(r"^Todo favorece que\b", re.IGNORECASE),
    ),
    (
        "repeticion-proyectos",
        re.compile(
            r"proyectos viables (?:genera|generan) proyectos",
            re.IGNORECASE,
        ),
    ),
    (
        "doble-finalidad-capital",
        re.compile(
            r"reservar capital para oportunidades favorables para actuar",
            re.IGNORECASE,
        ),
    ),
    (
        "doble-finalidad-fondos",
        re.compile(
            r"crear fondos para metas familiares importantes para financiar",
            re.IGNORECASE,
        ),
    ),
)


def normalize_phrase_template(text: str) -> str:
    """Corrige patrones sistemáticos sin alterar el significado de la frase."""
    cleaned = str(text or "").strip()
    for pattern, replacement in _SYSTEMATIC_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned, count=1)
    if cleaned.startswith("Las condiciones actuales impulsan una evolución favorable: "):
        cleaned = cleaned.replace(" y que ", " y ")
    for source, replacement in _EXACT_REPLACEMENTS:
        cleaned = cleaned.replace(source, replacement)

    if (
        cleaned.startswith("Para {{NOMBRE}}, ")
        and " se transforma en " in cleaned
        and " y en un paso firme hacia el éxito." in cleaned
    ):
        cleaned = cleaned.replace(" se transforma en ", " se convierte en ", 1)
        cleaned = cleaned.replace(
            " y en un paso firme hacia el éxito.",
            " y, al mismo tiempo, en un paso firme hacia el éxito.",
        )

    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned).strip()
    if cleaned and not cleaned.endswith(TERMINAL_PUNCTUATION):
        cleaned = f"{cleaned}."
    return cleaned


def phrase_quality_issues(text: str) -> list[str]:
    """Devuelve problemas conocidos que no deben entrar al catálogo activo."""
    issues = [issue_id for issue_id, pattern in _KNOWN_ISSUES if pattern.search(text)]
    if not str(text or "").strip().endswith(TERMINAL_PUNCTUATION):
        issues.append("sin-puntuacion-final")
    if re.search(r"\s{2,}", str(text or "")):
        issues.append("espacios-repetidos")
    return issues
