"""Versioned, explicit board-theme taxonomy with conservative fallbacks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stock_harness.board_hotspot_evaluation import canonical_board_name


BOARD_THEME_REGISTRY_VERSION = "board-theme-registry-v1"


@dataclass(frozen=True)
class BoardTheme:
    theme_id: str
    name: str
    level: str
    parent_theme_id: str | None = None
    description: str = ""


THEMES = (
    BoardTheme("power", "电力", "broad"),
    BoardTheme("power-generation", "发电", "subtheme", "power"),
    BoardTheme("power-grid", "电网", "subtheme", "power"),
    BoardTheme("medicine", "医药医疗", "broad"),
    BoardTheme("innovative-drug", "创新药", "subtheme", "medicine"),
    BoardTheme("cro", "医疗研发外包", "subtheme", "medicine"),
    BoardTheme("agriculture", "农业", "broad"),
    BoardTheme("agriculture-cultivation", "农业种植", "subtheme", "agriculture"),
    BoardTheme("seed-industry", "种业", "subtheme", "agriculture"),
    BoardTheme("hardware-technology", "大硬件科技", "broad"),
    BoardTheme("compute-power", "算力", "subtheme", "hardware-technology"),
    BoardTheme("cpo", "CPO", "subtheme", "hardware-technology"),
    BoardTheme("pcb", "PCB", "subtheme", "hardware-technology"),
    BoardTheme("semiconductor", "半导体", "subtheme", "hardware-technology"),
    BoardTheme(
        "consumer-electronics", "消费电子", "subtheme", "hardware-technology",
    ),
)


THEME_ALIASES: Mapping[str, tuple[str, ...]] = {
    "power-generation": ("电力", "绿色电力", "火力发电", "水力发电"),
    "power-grid": ("电网", "智能电网", "电网设备"),
    "innovative-drug": ("创新药", "创新药概念"),
    "cro": ("CRO", "CRO概念", "医疗研发外包", "临床CRO"),
    "agriculture-cultivation": ("农业种植", "种植业", "农作物"),
    "seed-industry": ("种业", "种子"),
    "compute-power": ("算力", "算力概念", "算力租赁"),
    "cpo": ("CPO", "CPO概念", "光模块"),
    "pcb": ("PCB", "PCB概念", "印制电路板"),
    "semiconductor": ("半导体", "半导体概念"),
    "consumer-electronics": ("消费电子", "消费电子概念"),
}


def theme_rows() -> list[dict[str, object]]:
    return [
        {
            "registry_version": BOARD_THEME_REGISTRY_VERSION,
            "theme_id": theme.theme_id,
            "theme_name": theme.name,
            "theme_level": theme.level,
            "parent_theme_id": theme.parent_theme_id,
            "description": theme.description,
        }
        for theme in THEMES
    ]


def alias_rows() -> list[dict[str, object]]:
    return [
        {
            "registry_version": BOARD_THEME_REGISTRY_VERSION,
            "normalized_alias": canonical_board_name(alias),
            "alias_name": alias,
            "theme_id": theme_id,
            "relation": "explicit-alias",
        }
        for theme_id, aliases in THEME_ALIASES.items()
        for alias in aliases
    ]


def resolve_board_theme_profiles(
    names: Mapping[str, str],
    *,
    themes: Sequence[Mapping[str, object]] | None = None,
    aliases: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    theme_source = list(themes) if themes is not None else theme_rows()
    alias_source = list(aliases) if aliases is not None else alias_rows()
    nodes = {str(row["theme_id"]): row for row in theme_source}
    alias_index = {
        str(row["normalized_alias"]): str(row["theme_id"])
        for row in alias_source
    }
    result: dict[str, dict[str, object]] = {}
    for symbol, name in names.items():
        normalized = canonical_board_name(name)
        theme_id = alias_index.get(normalized)
        node = nodes.get(theme_id or "")
        parent_id = str(node.get("parent_theme_id") or "") if node else ""
        parent = nodes.get(parent_id)
        result[symbol] = {
            "registry_version": BOARD_THEME_REGISTRY_VERSION,
            "theme_id": theme_id or f"board:{normalized}",
            "theme_name": str(node["theme_name"]) if node else name,
            "theme_level": str(node["theme_level"]) if node else "board",
            "parent_theme_id": parent_id or None,
            "parent_theme_name": str(parent["theme_name"]) if parent else None,
            "match_method": "explicit-alias" if node else "canonical-name-fallback",
            "normalized_board_name": normalized,
        }
    return result
