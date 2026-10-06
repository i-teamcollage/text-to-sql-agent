"""Serialize a query result to JSON and back without losing DuckDB's types.

The answer key stores each reference result as rows of cells. A cell is a plain JSON value when it
can be (int, float, str, bool, null) and a tagged object for the types JSON has no room for: decimals
(kept as text so no cent is lost), dates and timestamps. Decoding turns them back into the same Python
objects DuckDB hands back, so the stored reference and a live result are compared as like for like.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def encode_cell(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return {"t": "decimal", "v": str(value)}
    if isinstance(value, datetime):
        return {"t": "timestamp", "v": value.isoformat()}
    if isinstance(value, date):
        return {"t": "date", "v": value.isoformat()}
    raise TypeError(f"cannot encode {type(value).__name__}: {value!r}")


def decode_cell(value: Any) -> Any:
    if isinstance(value, dict) and "t" in value:
        kind, raw = value["t"], value["v"]
        if kind == "decimal":
            return Decimal(raw)
        if kind == "date":
            return date.fromisoformat(raw)
        if kind == "timestamp":
            return datetime.fromisoformat(raw)
        raise ValueError(f"unknown cell type {kind!r}")
    return value


def encode_rows(rows: list[tuple]) -> list[list]:
    return [[encode_cell(cell) for cell in row] for row in rows]


def decode_rows(rows: list[list]) -> list[tuple]:
    return [tuple(decode_cell(cell) for cell in row) for row in rows]


def canonical_order(rows: list[list]) -> list[list]:
    """A deterministic row order for results whose own order does not matter, so the stored answer
    key is byte-for-byte stable from one build to the next."""
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False))
