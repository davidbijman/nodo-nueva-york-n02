"""Adaptador económico chileno para el contrato normalizado de Sonantia."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ...sonantia_protocol import isoformat_utc

PROVIDER_ID = "chile-bcentral"
PROVIDER_LABEL = "Banco Central de Chile"
REGION_LABEL = "Chile"
COUNTRY_CODE = "CL"

BCENTRAL_MOBILE_URL = "https://si3.bcentral.cl/Bdemovil/BDE/IndicadoresDiarios"
SII_UTM_IPC_URL_TEMPLATE = "https://www.sii.cl/valores_y_fechas/utm/utm{year}.htm"
SANTIAGO_TZ = "America/Santiago"

MONTHS = {
    1: ("Enero", "ENE", "ene"),
    2: ("Febrero", "FEB", "feb"),
    3: ("Marzo", "MAR", "mar"),
    4: ("Abril", "ABR", "abr"),
    5: ("Mayo", "MAY", "may"),
    6: ("Junio", "JUN", "jun"),
    7: ("Julio", "JUL", "jul"),
    8: ("Agosto", "AGO", "ago"),
    9: ("Septiembre", "SEP", "sep"),
    10: ("Octubre", "OCT", "oct"),
    11: ("Noviembre", "NOV", "nov"),
    12: ("Diciembre", "DIC", "dic"),
}
MONTH_NUMBER_BY_NAME = {names[0]: number for number, names in MONTHS.items()}
MONTH_NUMBER_BY_ABBR = {names[2]: number for number, names in MONTHS.items()}


def _lines(markup: str) -> list[str]:
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", markup, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "\n", cleaned)
    text = html.unescape(cleaned)
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if re.sub(r"\s+", " ", line).strip()
    ]


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", without_marks.casefold()).strip()


def _value_after(lines: list[str], label: str) -> str | None:
    label_slug = _slug(label)
    for index, line in enumerate(lines):
        if _slug(line) != label_slug:
            continue
        for candidate in lines[index + 1 : index + 5]:
            if _number(candidate) is not None:
                return candidate
    return None


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = value.strip().replace("$", "").replace(" ", "")
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    try:
        return float(normalized)
    except ValueError:
        return None


def _format_clp(value: str | None) -> str | None:
    parsed = _number(value)
    if parsed is None:
        return None
    formatted = f"{parsed:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"${formatted}"


def _format_percent(value: str | None) -> str | None:
    parsed = _number(value)
    if parsed is None:
        return None
    formatted = f"{parsed:.2f}".replace(".", ",")
    formatted = formatted.rstrip("0").rstrip(",")
    return f"{formatted}%"


def _indicator(label: str, value: str | None, group: str = "general") -> dict[str, str]:
    return {"label": label, "value": value or "—", "group": group}


def _home_date(lines: list[str], moment: datetime) -> str:
    for line in lines:
        match = re.fullmatch(r"\((\d{1,2})-([a-z]{3})-(\d{4})\)", line, flags=re.IGNORECASE)
        if match:
            day, month_abbr, year = match.groups()
            month = MONTH_NUMBER_BY_ABBR.get(month_abbr.casefold())
            if month:
                return f"{int(day)} de {MONTHS[month][0].lower()} de {year}"
    local = moment.astimezone(ZoneInfo(SANTIAGO_TZ))
    return f"{local.day} de {MONTHS[local.month][0].lower()} de {local.year}"


def _extract_sii_rows(markup: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", markup, flags=re.IGNORECASE | re.DOTALL):
        row_markup = row_match.group(1)
        cells = re.findall(
            r"<t[dh][^>]*>(.*?)</t[dh]>",
            row_markup,
            flags=re.IGNORECASE | re.DOTALL,
        )
        values = [
            html.unescape(re.sub(r"<[^>]+>", " ", cell)).replace("\xa0", " ").strip()
            for cell in cells
        ]
        values = [re.sub(r"\s+", " ", value).strip() for value in values]
        if values and values[0] in MONTH_NUMBER_BY_NAME:
            rows[values[0]] = values
    return rows


def _sii_current_month(moment: datetime) -> str:
    return MONTHS[moment.astimezone(ZoneInfo(SANTIAGO_TZ)).month][0]


def _latest_ipc_row(rows: dict[str, list[str]], moment: datetime) -> tuple[str, list[str]] | None:
    current_month = MONTH_NUMBER_BY_NAME[_sii_current_month(moment)]
    candidates: list[tuple[int, str, list[str]]] = []
    for month_name, values in rows.items():
        month_number = MONTH_NUMBER_BY_NAME[month_name]
        if month_number > current_month or len(values) < 7:
            continue
        monthly, annual = values[4], values[6]
        if monthly and annual:
            candidates.append((month_number, month_name, values))
    if not candidates:
        return None
    _, month_name, values = max(candidates, key=lambda item: item[0])
    return month_name, values


def unavailable_bcentral_snapshot(moment: datetime, error: str | None = None) -> dict[str, Any]:
    generated_at = isoformat_utc(moment)
    return {
        "status": "unavailable",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": REGION_LABEL,
        "country_code": COUNTRY_CODE,
        "source_url": BCENTRAL_MOBILE_URL,
        "generated_at": generated_at,
        "observed_at": None,
        "date": "Indicadores oficiales",
        "indicators": [],
        "inflation": [],
        "error": error,
    }


def _merge_snapshots(
    moment: datetime,
    bcentral_markup: str | None,
    sii_markup: str | None,
    errors: list[str],
) -> dict[str, Any]:
    bcentral_lines = _lines(bcentral_markup or "")
    sii_rows = _extract_sii_rows(sii_markup or "")
    current_month_name = _sii_current_month(moment)
    current_month_abbr = MONTHS[MONTH_NUMBER_BY_NAME[current_month_name]][1]
    current_month_row = sii_rows.get(current_month_name, [])
    latest_ipc = _latest_ipc_row(sii_rows, moment)
    ipc_month_name, ipc_row = latest_ipc if latest_ipc else ("", [])
    ipc_abbr = MONTHS[MONTH_NUMBER_BY_NAME[ipc_month_name]][1] if ipc_month_name else ""

    current_utm = (
        f"${current_month_row[1]}" if len(current_month_row) > 1 and current_month_row[1] else None
    )
    indicators = [
        _indicator("UF", _format_clp(_value_after(bcentral_lines, "Unidad de Fomento (UF)"))),
        _indicator(f"UTM ({current_month_abbr})", current_utm),
        _indicator("Dólar observado", _format_clp(_value_after(bcentral_lines, "Dólar observado"))),
        _indicator("Euro", _format_clp(_value_after(bcentral_lines, "Euro (pesos por euro)"))),
    ]
    inflation = [
        _indicator(
            f"IPC ({ipc_abbr}) mensual" if ipc_abbr else "IPC mensual",
            ipc_row[4] if len(ipc_row) > 4 and ipc_row[4] else None,
            "inflation",
        ),
        _indicator(
            f"IPC ({ipc_abbr}) anual" if ipc_abbr else "IPC anual",
            ipc_row[6] if len(ipc_row) > 6 and ipc_row[6] else None,
            "inflation",
        ),
        _indicator(
            "TPM",
            _format_percent(_value_after(bcentral_lines, "Tasa de política monetaria (TPM)")),
            "inflation",
        ),
    ]
    has_values = any(item["value"] != "—" for item in indicators + inflation)
    if not has_values:
        return unavailable_bcentral_snapshot(moment, "; ".join(errors) or "sin datos")

    generated_at = isoformat_utc(moment)
    return {
        "status": "available",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": REGION_LABEL,
        "country_code": COUNTRY_CODE,
        "source_url": BCENTRAL_MOBILE_URL,
        "generated_at": generated_at,
        "observed_at": generated_at,
        "date": _home_date(bcentral_lines, moment),
        "indicators": indicators,
        "inflation": inflation,
        "error": "; ".join(errors) if errors else None,
    }


def fetch_bcentral_snapshot(
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    year = moment.astimezone(ZoneInfo(SANTIAGO_TZ)).year
    sii_url = SII_UTM_IPC_URL_TEMPLATE.format(year=year)
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    bcentral_markup: str | None = None
    sii_markup: str | None = None
    errors: list[str] = []
    try:
        try:
            response = active_client.get(BCENTRAL_MOBILE_URL, timeout=timeout_seconds)
            response.raise_for_status()
            bcentral_markup = response.text
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            errors.append(f"bcentral: {type(exc).__name__}: {exc}")
        try:
            response = active_client.get(sii_url, timeout=timeout_seconds)
            response.raise_for_status()
            sii_markup = response.text
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            errors.append(f"sii: {type(exc).__name__}: {exc}")
        return _merge_snapshots(moment, bcentral_markup, sii_markup, errors)
    finally:
        if owns_client:
            active_client.close()
