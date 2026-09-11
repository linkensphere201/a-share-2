"""Append-only persistence for result-bound AI conversations."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import re
from uuid import uuid4

from stock_harness.sqlite_mapping import _date_from_key, _date_key


_REFERENCE_PATTERN = re.compile(r"\[([KLPS]\d+)\]")


class SQLiteChatStoreMixin:
    def fail_interrupted_chat_turns(self) -> int:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            interrupted = [str(row[0]) for row in self._connection.execute(
                "SELECT turn_id FROM ai_chat_turns WHERE status IN ('queued', 'running')"
            ).fetchall()]
            cursor = self._connection.execute(
                """
                UPDATE ai_chat_turns
                SET status = 'failed', error = '应用退出时对话尚未完成',
                    completed_at_ms = ?
                WHERE status IN ('queued', 'running')
                """,
                (now_ms,),
            )
            for turn_id in interrupted:
                sequence = int(self._connection.execute(
                    "SELECT coalesce(max(sequence), 0) + 1 FROM ai_chat_stream_events WHERE turn_id = ?",
                    (turn_id,),
                ).fetchone()[0])
                self._connection.execute(
                    """INSERT INTO ai_chat_stream_events(
                           turn_id, sequence, event_type, data_json, created_at_ms
                       ) VALUES (?, ?, 'failed', ?, ?)""",
                    (turn_id, sequence, json.dumps({
                        "turn_id": turn_id, "message": "应用退出时对话尚未完成",
                    }, ensure_ascii=False), now_ms),
                )
        return int(cursor.rowcount)

    def get_or_create_chat_conversation(
        self, *, symbol: str, timeframe: str, source_run_id: str,
        force_new: bool = False,
    ) -> dict[str, object]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            identity = self._canonical_instrument_identity(symbol)
            if identity is None:
                raise ValueError(f"chat references unknown instrument: {symbol.strip()}")
            canonical_symbol, instrument_id = identity
            source = self._connection.execute(
                """
                SELECT instrument.symbol, run.timeframe, run.as_of_date
                FROM generated_analysis_runs AS run
                JOIN instruments AS instrument USING (instrument_id)
                WHERE run.run_id = ? AND run.status = 'succeeded'
                """,
                (source_run_id,),
            ).fetchone()
            if source is None:
                raise ValueError("chat source run does not exist or did not succeed")
            if str(source[0]) != canonical_symbol or str(source[1]) != timeframe:
                raise ValueError("chat source run belongs to another instrument or timeframe")
            existing = None if force_new else self._connection.execute(
                """
                SELECT conversation_id FROM ai_chat_conversations
                WHERE instrument_id = ? AND timeframe = ? AND source_run_id = ?
                  AND status = 'active'
                ORDER BY updated_at_ms DESC LIMIT 1
                """,
                (instrument_id, timeframe, source_run_id),
            ).fetchone()
            if existing is None:
                conversation_id = str(uuid4())
                self._connection.execute(
                    """
                    INSERT INTO ai_chat_conversations(
                        conversation_id, context_kind, context_id,
                        instrument_id, timeframe, source_run_id,
                        title, status, created_at_ms, updated_at_ms
                    ) VALUES (?, 'trend_analysis', ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        conversation_id, source_run_id, instrument_id, timeframe, source_run_id,
                        f"{canonical_symbol} · {_date_from_key(int(source[2])).isoformat()}",
                        now_ms, now_ms,
                    ),
                )
            else:
                conversation_id = str(existing[0])
                self._connection.execute(
                    """
                    UPDATE ai_chat_conversations
                    SET status = 'active', updated_at_ms = ?
                    WHERE conversation_id = ?
                    """,
                    (now_ms, conversation_id),
                )
        result = self.get_chat_conversation(conversation_id)
        assert result is not None
        return result

    def get_or_create_signal_chat_conversation(
        self, *, run_id: str, force_new: bool = False,
    ) -> dict[str, object]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source = self._connection.execute(
                """SELECT signal_id, effective_date, revision
                   FROM signal_review_runs
                   WHERE run_id = ? AND status = 'succeeded'""",
                (run_id,),
            ).fetchone()
            if source is None:
                raise ValueError("signal chat source run does not exist or did not succeed")
            existing = None if force_new else self._connection.execute(
                """SELECT conversation_id FROM ai_chat_conversations
                   WHERE context_kind = 'signal_run' AND context_id = ?
                     AND status = 'active'
                   ORDER BY updated_at_ms DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
            if existing is None:
                conversation_id = str(uuid4())
                title = (
                    f"{str(source[0])} · {_date_from_key(int(source[1])).isoformat()} "
                    f"R{int(source[2])}"
                )
                self._connection.execute(
                    """INSERT INTO ai_chat_conversations(
                           conversation_id, context_kind, context_id, title,
                           status, created_at_ms, updated_at_ms
                       ) VALUES (?, 'signal_run', ?, ?, 'active', ?, ?)""",
                    (conversation_id, run_id, title, now_ms, now_ms),
                )
            else:
                conversation_id = str(existing[0])
                self._connection.execute(
                    "UPDATE ai_chat_conversations SET updated_at_ms = ? WHERE conversation_id = ?",
                    (now_ms, conversation_id),
                )
        result = self.get_signal_chat_conversation(conversation_id)
        assert result is not None
        return result

    def list_signal_chat_conversations(
        self, *, run_id: str, include_archived: bool = True,
    ) -> list[dict[str, object]]:
        archived = "" if include_archived else "AND conversation.status = 'active'"
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT conversation.conversation_id, conversation.context_id,
                           conversation.title, conversation.status,
                           conversation.created_at_ms, conversation.updated_at_ms,
                           signal.effective_date,
                           (SELECT count(*) FROM ai_chat_turns AS turn
                            WHERE turn.conversation_id = conversation.conversation_id)
                    FROM ai_chat_conversations AS conversation
                    JOIN signal_review_runs AS signal ON signal.run_id = conversation.context_id
                    WHERE conversation.context_kind = 'signal_run'
                      AND conversation.context_id = ? {archived}
                    ORDER BY conversation.updated_at_ms DESC""",
                (run_id,),
            ).fetchall()
        return [{
            "conversation_id": str(row[0]), "context_kind": "signal_run",
            "context_id": str(row[1]), "source_run_id": None,
            "title": str(row[2]), "status": str(row[3]),
            "created_at_ms": int(row[4]), "updated_at_ms": int(row[5]),
            "as_of_date": _date_from_key(int(row[6])), "turn_count": int(row[7]),
        } for row in rows]

    def get_signal_chat_conversation(
        self, conversation_id: str,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT conversation.conversation_id, conversation.context_id,
                          conversation.title, conversation.codex_thread_id,
                          conversation.codex_policy_version, conversation.status,
                          conversation.created_at_ms, conversation.updated_at_ms,
                          signal.effective_date, signal.algorithm_version,
                          signal.input_digest
                   FROM ai_chat_conversations AS conversation
                   JOIN signal_review_runs AS signal ON signal.run_id = conversation.context_id
                   WHERE conversation.conversation_id = ?
                     AND conversation.context_kind = 'signal_run'""",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            turns = self._load_chat_turns_locked(conversation_id)
        return {
            "conversation_id": str(row[0]), "context_kind": "signal_run",
            "context_id": str(row[1]), "symbol": None, "timeframe": None,
            "source_run_id": None, "title": str(row[2]),
            "codex_thread_id": row[3], "codex_policy_version": row[4],
            "status": str(row[5]), "created_at_ms": int(row[6]),
            "updated_at_ms": int(row[7]), "as_of_date": _date_from_key(int(row[8])),
            "algorithm_version": str(row[9]), "config_version": "",
            "completion_state": "complete", "preview": False,
            "input_digest": str(row[10] or ""), "source_observed_at_ms": None,
            "turns": turns,
        }

    def get_or_create_signal_workspace_chat_conversation(
        self, *, signal_id: str, force_new: bool = False,
    ) -> dict[str, object]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            source = self._connection.execute(
                """SELECT effective_date FROM signal_review_runs
                   WHERE signal_id = ? AND status = 'succeeded'
                   ORDER BY effective_date DESC, revision DESC LIMIT 1""",
                (signal_id,),
            ).fetchone()
            if source is None:
                raise ValueError("signal workspace chat requires a succeeded review run")
            existing = None if force_new else self._connection.execute(
                """SELECT conversation_id FROM ai_chat_conversations
                   WHERE context_kind = 'signal_workspace' AND context_id = ?
                     AND status = 'active'
                   ORDER BY updated_at_ms DESC LIMIT 1""",
                (signal_id,),
            ).fetchone()
            if existing is None:
                conversation_id = str(uuid4())
                self._connection.execute(
                    """INSERT INTO ai_chat_conversations(
                           conversation_id, context_kind, context_id, title,
                           status, created_at_ms, updated_at_ms
                       ) VALUES (?, 'signal_workspace', ?, ?, 'active', ?, ?)""",
                    (conversation_id, signal_id, "信号复盘 · 多轮讨论", now_ms, now_ms),
                )
            else:
                conversation_id = str(existing[0])
                self._connection.execute(
                    "UPDATE ai_chat_conversations SET updated_at_ms = ? WHERE conversation_id = ?",
                    (now_ms, conversation_id),
                )
        result = self.get_signal_workspace_chat_conversation(conversation_id)
        assert result is not None
        return result

    def list_signal_workspace_chat_conversations(
        self, *, signal_id: str, include_archived: bool = True,
    ) -> list[dict[str, object]]:
        archived = "" if include_archived else "AND status = 'active'"
        with self._lock:
            latest = self._connection.execute(
                """SELECT effective_date FROM signal_review_runs
                   WHERE signal_id = ? AND status = 'succeeded'
                   ORDER BY effective_date DESC, revision DESC LIMIT 1""",
                (signal_id,),
            ).fetchone()
            rows = self._connection.execute(
                f"""SELECT conversation_id, context_id, title, status,
                           created_at_ms, updated_at_ms,
                           (SELECT count(*) FROM ai_chat_turns AS turn
                            WHERE turn.conversation_id = ai_chat_conversations.conversation_id)
                    FROM ai_chat_conversations
                    WHERE context_kind = 'signal_workspace' AND context_id = ? {archived}
                    ORDER BY updated_at_ms DESC""",
                (signal_id,),
            ).fetchall()
        as_of_date = _date_from_key(int(latest[0])) if latest else None
        return [{
            "conversation_id": str(row[0]), "context_kind": "signal_workspace",
            "context_id": str(row[1]), "source_run_id": None,
            "title": str(row[2]), "status": str(row[3]),
            "created_at_ms": int(row[4]), "updated_at_ms": int(row[5]),
            "as_of_date": as_of_date, "turn_count": int(row[6]),
        } for row in rows]

    def get_signal_workspace_chat_conversation(
        self, conversation_id: str,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT conversation_id, context_id, title, codex_thread_id,
                          codex_policy_version, status, created_at_ms, updated_at_ms
                   FROM ai_chat_conversations
                   WHERE conversation_id = ? AND context_kind = 'signal_workspace'""",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            latest = self._connection.execute(
                """SELECT effective_date, algorithm_version, input_digest
                   FROM signal_review_runs
                   WHERE signal_id = ? AND status = 'succeeded'
                   ORDER BY effective_date DESC, revision DESC LIMIT 1""",
                (str(row[1]),),
            ).fetchone()
            if latest is None:
                return None
            turns = self._load_chat_turns_locked(conversation_id)
        return {
            "conversation_id": str(row[0]), "context_kind": "signal_workspace",
            "context_id": str(row[1]), "symbol": None, "timeframe": None,
            "source_run_id": None, "title": str(row[2]),
            "codex_thread_id": row[3], "codex_policy_version": row[4],
            "status": str(row[5]), "created_at_ms": int(row[6]),
            "updated_at_ms": int(row[7]),
            "as_of_date": _date_from_key(int(latest[0])),
            "algorithm_version": str(latest[1]), "config_version": "",
            "completion_state": "complete", "preview": False,
            "input_digest": str(latest[2] or ""), "source_observed_at_ms": None,
            "turns": turns,
        }

    def get_or_create_learning_chat_conversation(
        self, *, system_id: str, title: str, force_new: bool = False,
    ) -> dict[str, object]:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self._lock, self._transaction():
            existing = None if force_new else self._connection.execute(
                """SELECT conversation_id FROM ai_chat_conversations
                   WHERE context_kind = 'learning_system' AND context_id = ?
                     AND status = 'active'
                   ORDER BY updated_at_ms DESC LIMIT 1""",
                (system_id,),
            ).fetchone()
            if existing is None:
                conversation_id = str(uuid4())
                self._connection.execute(
                    """INSERT INTO ai_chat_conversations(
                           conversation_id, context_kind, context_id, title,
                           status, created_at_ms, updated_at_ms
                       ) VALUES (?, 'learning_system', ?, ?, 'active', ?, ?)""",
                    (conversation_id, system_id, f"{title} · 学习讨论", now_ms, now_ms),
                )
            else:
                conversation_id = str(existing[0])
                self._connection.execute(
                    "UPDATE ai_chat_conversations SET updated_at_ms = ? WHERE conversation_id = ?",
                    (now_ms, conversation_id),
                )
        result = self.get_learning_chat_conversation(conversation_id)
        assert result is not None
        return result

    def list_learning_chat_conversations(
        self, *, system_id: str, include_archived: bool = True,
    ) -> list[dict[str, object]]:
        archived = "" if include_archived else "AND status = 'active'"
        with self._lock:
            rows = self._connection.execute(
                f"""SELECT conversation_id, context_id, title, status,
                           created_at_ms, updated_at_ms,
                           (SELECT count(*) FROM ai_chat_turns AS turn
                            WHERE turn.conversation_id = ai_chat_conversations.conversation_id)
                    FROM ai_chat_conversations
                    WHERE context_kind = 'learning_system' AND context_id = ? {archived}
                    ORDER BY updated_at_ms DESC""",
                (system_id,),
            ).fetchall()
        return [{
            "conversation_id": str(row[0]), "context_kind": "learning_system",
            "context_id": str(row[1]), "source_run_id": None,
            "title": str(row[2]), "status": str(row[3]),
            "created_at_ms": int(row[4]), "updated_at_ms": int(row[5]),
            "as_of_date": datetime.fromtimestamp(
                int(row[5]) / 1000, tz=timezone.utc
            ).date().isoformat(),
            "turn_count": int(row[6]),
        } for row in rows]

    def get_learning_chat_conversation(
        self, conversation_id: str,
    ) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT conversation_id, context_id, title, codex_thread_id,
                          codex_policy_version, status, created_at_ms, updated_at_ms
                   FROM ai_chat_conversations
                   WHERE conversation_id = ? AND context_kind = 'learning_system'""",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            turns = self._load_chat_turns_locked(conversation_id)
        as_of_date = datetime.fromtimestamp(
            int(row[7]) / 1000, tz=timezone.utc
        ).date().isoformat()
        return {
            "conversation_id": str(row[0]), "context_kind": "learning_system",
            "context_id": str(row[1]), "symbol": None, "timeframe": None,
            "source_run_id": None, "title": str(row[2]),
            "codex_thread_id": row[3], "codex_policy_version": row[4],
            "status": str(row[5]), "created_at_ms": int(row[6]),
            "updated_at_ms": int(row[7]), "as_of_date": as_of_date,
            "algorithm_version": "learning-visible-text-v1", "config_version": "",
            "completion_state": "complete", "preview": False,
            "input_digest": "", "source_observed_at_ms": None,
            "turns": turns,
        }

    def list_chat_conversations(
        self, *, symbol: str, timeframe: str, source_run_id: str | None = None,
        include_archived: bool = True,
    ) -> list[dict[str, object]]:
        filters = ["instrument.symbol = ? COLLATE NOCASE", "conversation.timeframe = ?"]
        values: list[object] = [symbol.strip().upper(), timeframe]
        if source_run_id is not None:
            filters.append("conversation.source_run_id = ?")
            values.append(source_run_id)
        if not include_archived:
            filters.append("conversation.status = 'active'")
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT conversation.conversation_id, conversation.source_run_id,
                       conversation.title, conversation.status,
                       conversation.created_at_ms, conversation.updated_at_ms,
                       run.as_of_date,
                       (SELECT count(*) FROM ai_chat_turns AS turn
                        WHERE turn.conversation_id = conversation.conversation_id)
                FROM ai_chat_conversations AS conversation
                JOIN instruments AS instrument USING (instrument_id)
                JOIN generated_analysis_runs AS run ON run.run_id = conversation.source_run_id
                WHERE {' AND '.join(filters)}
                ORDER BY conversation.updated_at_ms DESC
                """,
                values,
            ).fetchall()
        return [{
            "conversation_id": str(row[0]), "source_run_id": str(row[1]),
            "title": str(row[2]), "status": str(row[3]),
            "created_at_ms": int(row[4]), "updated_at_ms": int(row[5]),
            "as_of_date": _date_from_key(int(row[6])), "turn_count": int(row[7]),
        } for row in rows]

    def update_chat_conversation(
        self, conversation_id: str, *, title: str | None = None,
        status: str | None = None,
    ) -> dict[str, object] | None:
        if status not in {None, "active", "archived"}:
            raise ValueError("invalid chat conversation status")
        if title is not None and not title.strip():
            raise ValueError("chat conversation title cannot be empty")
        assignments = ["updated_at_ms = ?"]
        values: list[object] = [int(datetime.now(timezone.utc).timestamp() * 1000)]
        if title is not None:
            assignments.append("title = ?")
            values.append(title.strip())
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        values.append(conversation_id)
        with self._lock, self._transaction():
            cursor = self._connection.execute(
                f"UPDATE ai_chat_conversations SET {', '.join(assignments)} WHERE conversation_id = ?",
                values,
            )
        return self.get_chat_conversation(conversation_id) if cursor.rowcount else None

    def delete_chat_conversation(self, conversation_id: str) -> bool:
        with self._lock, self._transaction():
            active = self._connection.execute(
                """SELECT 1 FROM ai_chat_turns WHERE conversation_id = ?
                   AND status IN ('queued', 'running')""",
                (conversation_id,),
            ).fetchone()
            if active is not None:
                raise ValueError("cannot delete a conversation with an active turn")
            cursor = self._connection.execute(
                "DELETE FROM ai_chat_conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
        return bool(cursor.rowcount)

    def get_chat_conversation(self, conversation_id: str) -> dict[str, object] | None:
        with self._lock:
            kind = self._connection.execute(
                "SELECT context_kind FROM ai_chat_conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if kind is not None and str(kind[0]) == "signal_run":
                return self.get_signal_chat_conversation(conversation_id)
            if kind is not None and str(kind[0]) == "signal_workspace":
                return self.get_signal_workspace_chat_conversation(conversation_id)
            if kind is not None and str(kind[0]) == "learning_system":
                return self.get_learning_chat_conversation(conversation_id)
            row = self._connection.execute(
                """
                SELECT conversation.conversation_id, instrument.symbol,
                       conversation.timeframe, conversation.source_run_id,
                       conversation.title, conversation.codex_thread_id,
                       conversation.codex_policy_version,
                       conversation.status, conversation.created_at_ms,
                       conversation.updated_at_ms, run.as_of_date,
                       run.algorithm_version, run.config_version,
                       run.completion_state, run.expires_at_ms,
                       run.input_digest, run.source_observed_at_ms
                FROM ai_chat_conversations AS conversation
                JOIN instruments AS instrument USING (instrument_id)
                JOIN generated_analysis_runs AS run
                  ON run.run_id = conversation.source_run_id
                WHERE conversation.conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            turn_rows = self._connection.execute(
                """
                SELECT turn_id, codex_turn_id, template_id, template_version, status, error,
                       created_at_ms, completed_at_ms
                FROM ai_chat_turns WHERE conversation_id = ?
                ORDER BY created_at_ms, turn_id
                """,
                (conversation_id,),
            ).fetchall()
            turns: list[dict[str, object]] = []
            for turn in turn_rows:
                message_rows = self._connection.execute(
                    """
                    SELECT message_id, role, sequence, content, incomplete, created_at_ms
                    FROM ai_chat_messages WHERE turn_id = ? ORDER BY sequence
                    """,
                    (turn[0],),
                ).fetchall()
                turns.append({
                    "turn_id": str(turn[0]), "codex_turn_id": turn[1],
                    "template_id": turn[2], "template_version": turn[3],
                    "status": str(turn[4]), "error": turn[5],
                    "created_at_ms": int(turn[6]), "completed_at_ms": turn[7],
                    "messages": [{
                        "message_id": str(message[0]), "role": str(message[1]),
                        "sequence": int(message[2]), "content": str(message[3]),
                        "incomplete": bool(message[4]), "created_at_ms": int(message[5]),
                    } for message in message_rows],
                })
        return {
            "conversation_id": str(row[0]), "context_kind": "trend_analysis",
            "context_id": str(row[3]), "symbol": str(row[1]),
            "timeframe": str(row[2]), "source_run_id": str(row[3]),
            "title": str(row[4]), "codex_thread_id": row[5],
            "codex_policy_version": row[6],
            "status": str(row[7]), "created_at_ms": int(row[8]),
            "updated_at_ms": int(row[9]), "as_of_date": _date_from_key(int(row[10])),
            "algorithm_version": str(row[11]), "config_version": str(row[12]),
            "completion_state": str(row[13]), "preview": row[14] is not None,
            "input_digest": bytes(row[15]).hex(), "source_observed_at_ms": row[16],
            "turns": turns,
        }

    def _load_chat_turns_locked(self, conversation_id: str) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """SELECT turn_id, codex_turn_id, template_id, template_version,
                      status, error, created_at_ms, completed_at_ms
               FROM ai_chat_turns WHERE conversation_id = ?
               ORDER BY created_at_ms, turn_id""",
            (conversation_id,),
        ).fetchall()
        result = []
        for turn in rows:
            messages = self._connection.execute(
                """SELECT message_id, role, sequence, content, incomplete, created_at_ms
                   FROM ai_chat_messages WHERE turn_id = ? ORDER BY sequence""",
                (turn[0],),
            ).fetchall()
            result.append({
                "turn_id": str(turn[0]), "codex_turn_id": turn[1],
                "template_id": turn[2], "template_version": turn[3],
                "status": str(turn[4]), "error": turn[5],
                "created_at_ms": int(turn[6]), "completed_at_ms": turn[7],
                "messages": [{
                    "message_id": str(message[0]), "role": str(message[1]),
                    "sequence": int(message[2]), "content": str(message[3]),
                    "incomplete": bool(message[4]), "created_at_ms": int(message[5]),
                } for message in messages],
            })
        return result

    def set_chat_codex_thread(
        self,
        conversation_id: str,
        codex_thread_id: str,
        policy_version: str | None = None,
    ) -> None:
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE ai_chat_conversations
                SET codex_thread_id = ?, codex_policy_version = ?, updated_at_ms = ?
                WHERE conversation_id = ?
                """,
                (
                    codex_thread_id,
                    policy_version,
                    int(datetime.now(timezone.utc).timestamp() * 1000),
                    conversation_id,
                ),
            )

    def create_chat_turn(
        self,
        *,
        conversation_id: str,
        content: str,
        template_id: str | None,
        context: dict[str, object],
        template_version: str | None = None,
    ) -> dict[str, object]:
        turn_id = str(uuid4())
        message_id = str(uuid4())
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        context_kind = str(context.get("context_kind") or "trend_analysis")
        context_id = str(context.get("context_id") or context["source_run_id"])
        source_run_id = context.get("source_run_id")
        as_of_date = date.fromisoformat(str(context["as_of_date"]))
        with self._lock, self._transaction():
            conversation = self._connection.execute(
                """
                SELECT context_kind, context_id FROM ai_chat_conversations
                WHERE conversation_id = ? AND status = 'active'
                """,
                (conversation_id,),
            ).fetchone()
            if conversation is None:
                raise ValueError("chat conversation does not exist or is archived")
            if str(conversation[0]) != context_kind or str(conversation[1]) != context_id:
                raise ValueError("chat turn context does not match its conversation")
            active = self._connection.execute(
                """
                SELECT 1 FROM ai_chat_turns
                WHERE conversation_id = ? AND status IN ('queued', 'running')
                """,
                (conversation_id,),
            ).fetchone()
            if active is not None:
                raise RuntimeError("chat conversation already has an active turn")
            self._connection.execute(
                """
                INSERT INTO ai_chat_turns(
                    turn_id, conversation_id, template_id, template_version,
                    status, created_at_ms
                ) VALUES (?, ?, ?, ?, 'queued', ?)
                """,
                (turn_id, conversation_id, template_id, template_version, now_ms),
            )
            self._connection.execute(
                """
                INSERT INTO ai_chat_messages(
                    message_id, turn_id, role, sequence, content, created_at_ms
                ) VALUES (?, ?, 'user', 1, ?, ?)
                """,
                (message_id, turn_id, content.strip(), now_ms),
            )
            self._connection.execute(
                """
                INSERT INTO ai_chat_turn_contexts(
                    turn_id, schema_version, context_kind, context_id,
                    source_run_id, as_of_date,
                    input_digest, context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id, str(context["schema_version"]), context_kind, context_id,
                    str(source_run_id) if source_run_id else None,
                    _date_key(as_of_date),
                    str(context["input_digest"]),
                    json.dumps(context, ensure_ascii=False, sort_keys=True),
                ),
            )
        return {"turn_id": turn_id, "status": "queued", "created_at_ms": now_ms}

    def update_chat_turn(
        self,
        turn_id: str,
        status: str,
        *,
        codex_turn_id: str | None = None,
        error: str | None = None,
        assistant_content: str | None = None,
    ) -> None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        terminal = status in {"completed", "failed", "cancelled"}
        with self._lock, self._transaction():
            self._connection.execute(
                """
                UPDATE ai_chat_turns
                SET status = ?, codex_turn_id = coalesce(?, codex_turn_id),
                    error = ?, completed_at_ms = CASE WHEN ? THEN ? ELSE NULL END
                WHERE turn_id = ?
                """,
                (status, codex_turn_id, error, int(terminal), now_ms, turn_id),
            )
            if assistant_content is not None:
                existing_message = self._connection.execute(
                    "SELECT message_id FROM ai_chat_messages WHERE turn_id = ? AND sequence = 2",
                    (turn_id,),
                ).fetchone()
                message_id = str(existing_message[0]) if existing_message else str(uuid4())
                if existing_message is None:
                    self._connection.execute(
                        """
                        INSERT INTO ai_chat_messages(
                            message_id, turn_id, role, sequence, content,
                            incomplete, created_at_ms
                        ) VALUES (?, ?, 'assistant', 2, ?, ?, ?)
                        """,
                        (
                            message_id, turn_id, assistant_content,
                            int(status != "completed"), now_ms,
                        ),
                    )
                else:
                    self._connection.execute(
                        """UPDATE ai_chat_messages
                           SET content = ?, incomplete = ?, created_at_ms = ?
                           WHERE message_id = ?""",
                        (assistant_content, int(status != "completed"), now_ms, message_id),
                    )
                    self._connection.execute(
                        "DELETE FROM ai_chat_message_references WHERE message_id = ?",
                        (message_id,),
                    )
                context = self._connection.execute(
                    "SELECT context_json FROM ai_chat_turn_contexts WHERE turn_id = ?",
                    (turn_id,),
                ).fetchone()
                if context is not None:
                    evidence = json.loads(str(context[0])).get("evidence", [])
                    item_by_code = {
                        str(item.get("code")): str(item.get("analysis_item_id"))
                        for item in evidence if item.get("analysis_item_id")
                    }
                    for code in dict.fromkeys(_REFERENCE_PATTERN.findall(assistant_content)):
                        if code in item_by_code:
                            self._connection.execute(
                                """
                                INSERT INTO ai_chat_message_references(
                                    message_id, code, analysis_item_id
                                ) VALUES (?, ?, ?)
                                """,
                                (message_id, code, item_by_code[code]),
                            )
            self._connection.execute(
                """
                UPDATE ai_chat_conversations SET updated_at_ms = ?
                WHERE conversation_id = (
                    SELECT conversation_id FROM ai_chat_turns WHERE turn_id = ?
                )
                """,
                (now_ms, turn_id),
            )

    def get_chat_turn_context(self, turn_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT context_json FROM ai_chat_turn_contexts WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
        return json.loads(str(row[0])) if row is not None else None

    def get_chat_turn(self, turn_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT turn.conversation_id, turn.template_id,
                       turn.template_version, message.content
                FROM ai_chat_turns AS turn
                JOIN ai_chat_messages AS message ON message.turn_id = turn.turn_id
                WHERE turn.turn_id = ? AND message.role = 'user'
                """,
                (turn_id,),
            ).fetchone()
        return None if row is None else {
            "conversation_id": str(row[0]), "template_id": row[1],
            "template_version": row[2], "content": str(row[3]),
        }

    def append_chat_stream_event(
        self, turn_id: str, sequence: int, event_type: str, data: dict[str, object]
    ) -> None:
        with self._lock, self._transaction():
            self._connection.execute(
                """INSERT OR IGNORE INTO ai_chat_stream_events(
                       turn_id, sequence, event_type, data_json, created_at_ms
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    turn_id, sequence, event_type,
                    json.dumps(data, ensure_ascii=False, sort_keys=True),
                    int(datetime.now(timezone.utc).timestamp() * 1000),
                ),
            )

    def list_chat_stream_events(self, turn_id: str) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT sequence, event_type, data_json
                   FROM ai_chat_stream_events WHERE turn_id = ? ORDER BY sequence""",
                (turn_id,),
            ).fetchall()
        return [{
            "sequence": int(row[0]), "type": str(row[1]),
            "data": json.loads(str(row[2])),
        } for row in rows]
