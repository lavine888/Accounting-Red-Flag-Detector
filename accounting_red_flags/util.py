"""Small shared parsing helpers."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

import pandas as pd

_QUARTER_RE = re.compile(r"^(\d{4})q([1-4])$", re.I)


def clean_symbol(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    symbol = str(value).strip().upper()
    if not symbol:
        return None
    return symbol[:-3] + ".SH" if symbol.endswith(".SS") else symbol


def clean_date(value: Any) -> str:
    """Normalize a date to ``YYYYMMDD``. Invalid or empty input returns ``""``."""

    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if re.fullmatch(r"\d{8}", text):
        cleaned = text
    elif re.match(r"^\d{4}-\d{2}-\d{2}", text):
        cleaned = text[:10].replace("-", "")
    else:
        return ""
    try:
        datetime.strptime(cleaned, "%Y%m%d")
    except ValueError:
        return ""
    return cleaned


def quarter_key(value: Any) -> tuple[int, int] | None:
    match = _QUARTER_RE.match(str(value).strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def is_valid_symbol(value: str | None) -> bool:
    return bool(value) and bool(re.fullmatch(r"\d{6}\.(SH|SZ)", str(value)))
