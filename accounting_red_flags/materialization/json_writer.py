"""Strict JSON output helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4


def canonical_json(value: Any) -> str:
    """Deterministic JSON with no NaN/Infinity leakage."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)


def dumps(result: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(result, ensure_ascii=False, indent=indent, allow_nan=False, default=str)


def write_json(result: dict[str, Any], path: str | Path, *, indent: int = 2) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(dumps(result, indent=indent) + "\n", encoding="utf-8")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
