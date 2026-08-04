"""Derived search terms for Chinese instrument names."""

from __future__ import annotations

import re

from pypinyin import Style, lazy_pinyin


_SEARCH_CHARS = re.compile(r"[^0-9a-z]+")


def normalize_search_term(value: str) -> str:
    return _SEARCH_CHARS.sub("", value.casefold())


def pinyin_search_aliases(value: str) -> tuple[tuple[str, str], ...]:
    if not value or not any("\u3400" <= char <= "\u9fff" for char in value):
        return ()
    aliases: list[tuple[str, str]] = []
    for alias_type, style in (
        ("pinyin_full", Style.NORMAL),
        ("pinyin_initials", Style.FIRST_LETTER),
    ):
        alias = normalize_search_term("".join(lazy_pinyin(value, style=style, errors=lambda chars: list(chars))))
        if alias and all(alias != existing for existing, _ in aliases):
            aliases.append((alias, alias_type))
    return tuple(aliases)


def matches_name_or_pinyin(query: str, *values: str) -> bool:
    direct = query.strip().casefold()
    normalized = normalize_search_term(query)
    for value in values:
        if direct in value.casefold():
            return True
        if normalized and any(normalized in alias for alias, _ in pinyin_search_aliases(value)):
            return True
    return False
