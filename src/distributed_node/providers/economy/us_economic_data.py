"""Adaptador económico de Estados Unidos para el contrato normalizado de Sonantia."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from io import StringIO
from typing import Any

import httpx

from ...sonantia_protocol import isoformat_utc

PROVIDER_ID = "us-economic-data"
PROVIDER_LABEL = "Federal Reserve Economic Data (FRED)"
REGION_LABEL = "Estados Unidos"
COUNTRY_CODE = "US"

FRED_HOME_URL = "https://fred.stlouisfed.org/"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SERIES_EURO = "DEXUSEU"
SERIES_POUND = "DEXUSUK"
SERIES_YEN = "DEXJPUS"
SERIES_CANADIAN_DOLLAR = "DEXCAUS"
SERIES_CPI = "CPIAUCNS"
SERIES_FED_RATE = "DFEDTARU"


def _indicator(label: str, value: str | None, group: str = "general") -> dict[str, str]:
    return {"label": label, "value": value or "—", "group": group}


def _format_decimal(value: float | None, *, digits: int = 4) -> str | None:
    if value is None:
        return None
    return f"{value:.{digits}f}".replace(".", ",")


def _format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    formatted = f"{value:.2f}".replace(".", ",").rstrip("0").rstrip(",")
    return f"{formatted}%"


def _parse_series(markup: str, series_id: str) -> list[tuple[str, float]]:
    reader = csv.DictReader(StringIO(markup.lstrip("\ufeff")))
    if not reader.fieldnames or len(reader.fieldnames) < 2:
        raise ValueError(f"FRED no entregó columnas para {series_id}")

    date_key = reader.fieldnames[0]
    value_key = series_id if series_id in reader.fieldnames else reader.fieldnames[1]
    observations: list[tuple[str, float]] = []
    for row in reader:
        date = str(row.get(date_key) or "").strip()
        raw_value = str(row.get(value_key) or "").strip()
        if not date or raw_value in {"", "."}:
            continue
        try:
            observations.append((date, float(raw_value)))
        except ValueError:
            continue
    if not observations:
        raise ValueError(f"FRED no entregó observaciones para {series_id}")
    observations.sort(key=lambda item: item[0])
    return observations


def _latest_value(series: list[tuple[str, float]]) -> tuple[str, float]:
    return series[-1]


def _percentage_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return ((current / previous) - 1) * 100


def unavailable_us_economic_snapshot(
    moment: datetime,
    error: str | None = None,
) -> dict[str, Any]:
    generated_at = isoformat_utc(moment)
    return {
        "status": "unavailable",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": REGION_LABEL,
        "country_code": COUNTRY_CODE,
        "source_url": FRED_HOME_URL,
        "generated_at": generated_at,
        "observed_at": None,
        "date": "Indicadores oficiales de Estados Unidos",
        "indicators": [],
        "inflation": [],
        "error": error,
    }


def _snapshot_from_series(
    moment: datetime,
    series: dict[str, list[tuple[str, float]]],
    errors: list[str],
) -> dict[str, Any]:
    euro_date, euro = _latest_value(series[SERIES_EURO]) if SERIES_EURO in series else ("", None)
    pound_date, pound = (
        _latest_value(series[SERIES_POUND])
        if SERIES_POUND in series
        else ("", None)
    )
    yen_date, yen = _latest_value(series[SERIES_YEN]) if SERIES_YEN in series else ("", None)
    cad_date, cad = (
        _latest_value(series[SERIES_CANADIAN_DOLLAR])
        if SERIES_CANADIAN_DOLLAR in series
        else ("", None)
    )

    monthly_inflation = annual_inflation = None
    cpi_date = ""
    cpi = series.get(SERIES_CPI, [])
    if cpi:
        cpi_date, latest_cpi = cpi[-1]
        if len(cpi) >= 2:
            monthly_inflation = _percentage_change(latest_cpi, cpi[-2][1])
        if len(cpi) >= 13:
            annual_inflation = _percentage_change(latest_cpi, cpi[-13][1])

    rate_date, fed_rate = (
        _latest_value(series[SERIES_FED_RATE])
        if SERIES_FED_RATE in series
        else ("", None)
    )

    indicators = [
        _indicator("Euro (USD por EUR)", _format_decimal(euro)),
        _indicator("Libra (USD por GBP)", _format_decimal(pound)),
        _indicator("Yen (JPY por USD)", _format_decimal(yen, digits=2)),
        _indicator("Dólar canadiense", _format_decimal(cad)),
    ]
    inflation = [
        _indicator("IPC mensual", _format_percent(monthly_inflation), "inflation"),
        _indicator("IPC anual", _format_percent(annual_inflation), "inflation"),
        _indicator("Tasa Fed", _format_percent(fed_rate), "inflation"),
    ]

    has_values = any(item["value"] != "—" for item in indicators + inflation)
    if not has_values:
        return unavailable_us_economic_snapshot(moment, "; ".join(errors) or "sin datos")

    observed_date = max(
        (
            date
            for date in (euro_date, pound_date, yen_date, cad_date, cpi_date, rate_date)
            if date
        ),
        default="",
    )
    generated_at = isoformat_utc(moment)
    observed_at = f"{observed_date}T00:00:00Z" if observed_date else generated_at
    return {
        "status": "available",
        "provider": PROVIDER_ID,
        "provider_label": PROVIDER_LABEL,
        "region_label": REGION_LABEL,
        "country_code": COUNTRY_CODE,
        "source_url": FRED_HOME_URL,
        "generated_at": generated_at,
        "observed_at": observed_at,
        "date": (
            f"Indicadores oficiales · {observed_date}"
            if observed_date
            else "Indicadores oficiales de Estados Unidos"
        ),
        "indicators": indicators,
        "inflation": inflation,
        "error": "; ".join(errors) if errors else None,
    }


def fetch_us_economic_snapshot(
    moment: datetime,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    owns_client = client is None
    active_client = client or httpx.Client(follow_redirects=True)
    series: dict[str, list[tuple[str, float]]] = {}
    errors: list[str] = []
    try:
        for series_id in (
            SERIES_EURO,
            SERIES_POUND,
            SERIES_YEN,
            SERIES_CANADIAN_DOLLAR,
            SERIES_CPI,
            SERIES_FED_RATE,
        ):
            try:
                response = active_client.get(
                    FRED_CSV_URL,
                    params={
                        "id": series_id,
                        "cosd": f"{(moment - timedelta(days=400)):%Y-%m-%d}",
                        "coed": f"{moment:%Y-%m-%d}",
                    },
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                series[series_id] = _parse_series(response.text, series_id)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                errors.append(f"{series_id}: {type(exc).__name__}: {exc}")
        return _snapshot_from_series(moment, series, errors)
    finally:
        if owns_client:
            active_client.close()
