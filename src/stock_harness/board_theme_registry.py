"""Versioned, explicit board-theme taxonomy with conservative fallbacks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stock_harness.board_hotspot_evaluation import canonical_board_name


BOARD_THEME_REGISTRY_VERSION = "board-theme-registry-v2-trading-leaves"


@dataclass(frozen=True)
class BoardTheme:
    theme_id: str
    name: str
    level: str
    parent_theme_id: str | None = None
    description: str = ""


THEMES = (
    BoardTheme("power", "电力", "broad"),
    BoardTheme("thermal-power", "火力发电", "subtheme", "power"),
    BoardTheme("hydro-power", "水力发电", "subtheme", "power"),
    BoardTheme("nuclear-power", "核电", "subtheme", "power"),
    BoardTheme("green-power", "绿色电力", "subtheme", "power"),
    BoardTheme("wind-power-generation", "风力发电", "subtheme", "power"),
    BoardTheme("power-grid-equipment", "电网设备", "subtheme", "power"),
    BoardTheme("ultra-high-voltage", "特高压", "subtheme", "power"),
    BoardTheme("smart-grid", "智能电网", "subtheme", "power"),
    BoardTheme("virtual-power-plant", "虚拟电厂", "subtheme", "power"),
    BoardTheme("medicine", "医药医疗", "broad"),
    BoardTheme("innovative-drug", "创新药", "subtheme", "medicine"),
    BoardTheme("cro", "医疗研发外包", "subtheme", "medicine"),
    BoardTheme("cdmo", "CDMO", "subtheme", "medicine"),
    BoardTheme("weight-loss-drug", "减肥药", "subtheme", "medicine"),
    BoardTheme("traditional-chinese-medicine", "中药", "subtheme", "medicine"),
    BoardTheme("active-pharmaceutical-ingredient", "原料药", "subtheme", "medicine"),
    BoardTheme("generic-drug", "仿制药", "subtheme", "medicine"),
    BoardTheme("medical-service", "医疗服务", "subtheme", "medicine"),
    BoardTheme("medical-device", "医疗器械", "subtheme", "medicine"),
    BoardTheme("pharmaceutical-commerce", "医药商业", "subtheme", "medicine"),
    BoardTheme("agriculture", "农业", "broad"),
    BoardTheme("seed-industry", "种业", "subtheme", "agriculture"),
    BoardTheme("genetically-modified", "转基因", "subtheme", "agriculture"),
    BoardTheme("agriculture-cultivation", "农业种植", "subtheme", "agriculture"),
    BoardTheme("pesticide", "农药", "subtheme", "agriculture"),
    BoardTheme("fertilizer", "化肥", "subtheme", "agriculture"),
    BoardTheme("hog-farming", "生猪养殖", "subtheme", "agriculture"),
    BoardTheme("poultry-farming", "禽类养殖", "subtheme", "agriculture"),
    BoardTheme("aquaculture", "水产养殖", "subtheme", "agriculture"),
    BoardTheme("animal-feed", "饲料", "subtheme", "agriculture"),
    BoardTheme("animal-health", "动物保健", "subtheme", "agriculture"),
    BoardTheme("agricultural-machinery", "农业机械", "subtheme", "agriculture"),
    BoardTheme("agricultural-processing", "农产品加工", "subtheme", "agriculture"),
    BoardTheme("hardware-technology", "大硬件科技", "broad"),
    BoardTheme("compute-power", "算力", "subtheme", "hardware-technology"),
    BoardTheme("compute-power-rental", "算力租赁", "subtheme", "hardware-technology"),
    BoardTheme("cpo", "CPO", "subtheme", "hardware-technology"),
    BoardTheme("optical-module", "光模块", "subtheme", "hardware-technology"),
    BoardTheme("optical-chip", "光芯片", "subtheme", "hardware-technology"),
    BoardTheme("pcb", "PCB", "subtheme", "hardware-technology"),
    BoardTheme("copper-clad-laminate", "覆铜板", "subtheme", "hardware-technology"),
    BoardTheme("high-speed-copper", "高速铜连接", "subtheme", "hardware-technology"),
    BoardTheme("semiconductor", "半导体", "subtheme", "hardware-technology"),
    BoardTheme("semiconductor-equipment", "半导体设备", "subtheme", "hardware-technology"),
    BoardTheme("semiconductor-material", "半导体材料", "subtheme", "hardware-technology"),
    BoardTheme("chip-design", "芯片设计", "subtheme", "hardware-technology"),
    BoardTheme("wafer-manufacturing", "晶圆制造", "subtheme", "hardware-technology"),
    BoardTheme("semiconductor-packaging", "封装测试", "subtheme", "hardware-technology"),
    BoardTheme("advanced-packaging", "先进封装", "subtheme", "hardware-technology"),
    BoardTheme("hbm", "HBM", "subtheme", "hardware-technology"),
    BoardTheme("liquid-cooling", "液冷", "subtheme", "hardware-technology"),
    BoardTheme("server", "服务器", "subtheme", "hardware-technology"),
    BoardTheme("data-center", "数据中心", "subtheme", "hardware-technology"),
    BoardTheme("consumer-electronics", "消费电子", "subtheme", "hardware-technology"),
)


THEME_ALIASES: Mapping[str, tuple[str, ...]] = {
    "thermal-power": ("火力发电", "火电"),
    "hydro-power": ("水力发电", "水电"),
    "nuclear-power": ("核电", "核电概念"),
    "green-power": ("绿色电力", "绿色电力概念"),
    "wind-power-generation": ("风力发电",),
    "power-grid-equipment": ("电网设备",),
    "ultra-high-voltage": ("特高压", "特高压概念"),
    "smart-grid": ("智能电网", "智能电网概念"),
    "virtual-power-plant": ("虚拟电厂", "虚拟电厂概念"),
    "innovative-drug": ("创新药", "创新药概念"),
    "cro": ("CRO", "CRO概念", "医疗研发外包", "临床CRO"),
    "cdmo": ("CDMO", "CDMO概念"),
    "weight-loss-drug": ("减肥药", "减肥药概念"),
    "traditional-chinese-medicine": ("中药", "中药概念"),
    "active-pharmaceutical-ingredient": ("原料药", "原料药概念"),
    "generic-drug": ("仿制药", "仿制药概念"),
    "medical-service": ("医疗服务",),
    "medical-device": ("医疗器械", "医疗器械概念"),
    "pharmaceutical-commerce": ("医药商业",),
    "seed-industry": ("种业", "种子"),
    "genetically-modified": ("转基因", "转基因概念"),
    "agriculture-cultivation": ("农业种植", "种植业", "农作物"),
    "pesticide": ("农药", "农药概念"),
    "fertilizer": ("化肥", "化肥概念"),
    "hog-farming": ("生猪养殖", "猪产业"),
    "poultry-farming": ("禽类养殖", "鸡产业"),
    "aquaculture": ("水产养殖",),
    "animal-feed": ("饲料",),
    "animal-health": ("动物保健",),
    "agricultural-machinery": ("农业机械", "农机"),
    "agricultural-processing": ("农产品加工",),
    "compute-power": ("算力", "算力概念"),
    "compute-power-rental": ("算力租赁", "算力租赁概念"),
    "cpo": ("CPO", "CPO概念"),
    "optical-module": ("光模块", "光模块概念"),
    "optical-chip": ("光芯片", "光芯片概念"),
    "pcb": ("PCB", "PCB概念", "印制电路板"),
    "copper-clad-laminate": ("覆铜板", "覆铜板概念"),
    "high-speed-copper": ("高速铜连接", "铜缆高速连接"),
    "semiconductor": ("半导体", "半导体概念"),
    "semiconductor-equipment": ("半导体设备",),
    "semiconductor-material": ("半导体材料",),
    "chip-design": ("芯片设计",),
    "wafer-manufacturing": ("晶圆制造",),
    "semiconductor-packaging": ("封装测试",),
    "advanced-packaging": ("先进封装", "先进封装概念"),
    "hbm": ("HBM", "高带宽内存"),
    "liquid-cooling": ("液冷", "液冷服务器"),
    "server": ("服务器",),
    "data-center": ("数据中心", "数据中心(AIDC)"),
    "consumer-electronics": ("消费电子", "消费电子概念"),
}


TAXONOMY_ALIASES: Mapping[str, tuple[str, ...]] = {
    "power": ("电力",),
    "medicine": ("医药", "医药医疗"),
    "agriculture": ("农业",),
    "hardware-technology": ("大硬件科技", "硬件科技"),
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
    rows: list[dict[str, object]] = []
    seen: dict[str, str] = {}
    for relation, source in (
        ("explicit-alias", THEME_ALIASES),
        ("taxonomy-only", TAXONOMY_ALIASES),
    ):
        for theme_id, aliases in source.items():
            for alias in aliases:
                normalized = canonical_board_name(alias)
                existing = seen.get(normalized)
                if existing is not None and existing != theme_id:
                    raise ValueError(
                        f"board theme alias {alias!r} maps to multiple themes"
                    )
                seen[normalized] = theme_id
                rows.append({
                    "registry_version": BOARD_THEME_REGISTRY_VERSION,
                    "normalized_alias": normalized,
                    "alias_name": alias,
                    "theme_id": theme_id,
                    "relation": relation,
                })
    return rows


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
        str(row["normalized_alias"]): row
        for row in alias_source
    }
    result: dict[str, dict[str, object]] = {}
    for symbol, name in names.items():
        normalized = canonical_board_name(name)
        alias = alias_index.get(normalized)
        theme_id = str(alias["theme_id"]) if alias else None
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
            "match_method": (
                str(alias["relation"]) if alias else "canonical-name-fallback"
            ),
            "signal_eligible": not alias or alias["relation"] != "taxonomy-only",
            "normalized_board_name": normalized,
        }
    return result
