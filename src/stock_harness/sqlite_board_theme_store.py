"""SQLite persistence for immutable board-theme registry versions."""

from __future__ import annotations

from collections.abc import Mapping

from stock_harness.board_theme_registry import (
    BOARD_THEME_REGISTRY_VERSION, alias_rows, resolve_board_theme_profiles,
    theme_rows,
)


class SQLiteBoardThemeStoreMixin:
    def ensure_board_theme_registry(self) -> None:
        themes = theme_rows()
        aliases = alias_rows()
        with self._lock, self._writer_lock, self._connection:
            self._connection.executemany(
                """
                INSERT OR IGNORE INTO board_theme_nodes(
                    registry_version, theme_id, theme_name, theme_level,
                    parent_theme_id, description
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [(
                    row["registry_version"], row["theme_id"], row["theme_name"],
                    row["theme_level"], row["parent_theme_id"], row["description"],
                ) for row in themes],
            )
            self._connection.executemany(
                """
                INSERT OR IGNORE INTO board_theme_aliases(
                    registry_version, normalized_alias, alias_name,
                    theme_id, relation
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [(
                    row["registry_version"], row["normalized_alias"],
                    row["alias_name"], row["theme_id"], row["relation"],
                ) for row in aliases],
            )

    def resolve_board_theme_profiles(
        self, names: Mapping[str, str],
        registry_version: str = BOARD_THEME_REGISTRY_VERSION,
    ) -> dict[str, dict[str, object]]:
        with self._lock:
            themes = [dict(row) for row in self._connection.execute(
                "SELECT * FROM board_theme_nodes WHERE registry_version = ?",
                (registry_version,),
            )]
            aliases = [dict(row) for row in self._connection.execute(
                "SELECT * FROM board_theme_aliases WHERE registry_version = ?",
                (registry_version,),
            )]
        if not themes:
            raise ValueError(f"unknown board theme registry: {registry_version}")
        return resolve_board_theme_profiles(names, themes=themes, aliases=aliases)

    def list_board_theme_registry_versions(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT node.registry_version, count(DISTINCT node.theme_id),
                       count(DISTINCT alias.normalized_alias)
                FROM board_theme_nodes AS node
                LEFT JOIN board_theme_aliases AS alias
                  ON alias.registry_version = node.registry_version
                 AND alias.theme_id = node.theme_id
                GROUP BY node.registry_version
                ORDER BY node.registry_version DESC
                """
            ).fetchall()
        return [{
            "registry_version": str(row[0]),
            "theme_count": int(row[1]),
            "alias_count": int(row[2]),
            "current": str(row[0]) == BOARD_THEME_REGISTRY_VERSION,
        } for row in rows]
