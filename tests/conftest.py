"""Aislamiento de temporales para ejecuciones locales, Codex y GitHub Actions."""

from __future__ import annotations

from tempfile import TemporaryDirectory

import pytest

_session_temp: TemporaryDirectory[str] | None = None


def pytest_configure(config: pytest.Config) -> None:
    """Evita reutilizar el directorio global pytest-of-<usuario> de Windows."""
    global _session_temp
    if config.option.basetemp is None:
        _session_temp = TemporaryDirectory(prefix="nodos-web-pytest-")
        config.option.basetemp = _session_temp.name


def pytest_unconfigure(config: pytest.Config) -> None:
    """Elimina solo el temporal privado creado por esta sesión."""
    global _session_temp
    if _session_temp is not None:
        _session_temp.cleanup()
        _session_temp = None
