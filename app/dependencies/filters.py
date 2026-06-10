from collections import defaultdict
from typing import Any

from fastapi import Request


async def parse_b2b_filters_query(request: Request) -> dict[str, Any] | None:
    """Парсит deepObject-параметры вида filters[brand]=Apple (B2B internal)."""
    collected: dict[str, list[str]] = defaultdict(list)
    for key, value in request.query_params.multi_items():
        if key.startswith("filters[") and key.endswith("]"):
            slug = key[len("filters[") : -1]
            collected[slug].append(value)
    if not collected:
        return None
    return {
        slug: values[0] if len(values) == 1 else values
        for slug, values in collected.items()
    }


async def parse_catalog_filters_query(request: Request) -> dict[str, Any] | None:
    """Парсит deepObject-параметры вида filter[brand]=Apple (B2C public API)."""
    collected: dict[str, list[str]] = defaultdict(list)
    for key, value in request.query_params.multi_items():
        if key.startswith("filter[") and key.endswith("]"):
            slug = key[len("filter[") : -1]
            collected[slug].append(value)
    if not collected:
        return None
    return {
        slug: values[0] if len(values) == 1 else values
        for slug, values in collected.items()
    }
