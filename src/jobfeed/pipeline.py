"""Extract, validate, deduplicate and load one batch."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from .quality import validate
from .store import Warehouse
from .transform import dedupe, normalize


def run(
    warehouse: Warehouse,
    records: Iterable[dict[str, Any]],
    as_of: datetime,
    source: str,
) -> dict[str, int]:
    """Process a batch. The feed is read fully first, so a failed fetch loads nothing."""
    raw = list(records)
    valid: list[dict[str, Any]] = []
    rejected: list[tuple[list[str], dict[str, Any]]] = []
    for rec in raw:
        row = normalize(rec)
        problems = validate(row, as_of)
        if problems:
            rejected.append((problems, rec))
        else:
            valid.append(row)
    unique = dedupe(valid)
    return warehouse.load(
        uuid.uuid4().hex, as_of, source, raw, rejected, unique, len(valid) - len(unique)
    )
