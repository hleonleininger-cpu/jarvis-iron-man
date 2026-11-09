"""Kleine Helfer, um untypisiertes JSON der Notion-API streng typisiert zu lesen."""

from typing import Any, cast


def as_dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []
