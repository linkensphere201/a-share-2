"""Result-bound Codex chat routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from stock_harness.api_models import (
    AiChatConversationInput, AiChatConversationUpdateInput, AiChatTurnInput,
)


def create_chat_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/ai/codex/status")
    def codex_status(request: Request) -> dict[str, object]:
        return request.app.state.chat_service.capabilities()

    @router.post("/api/ai/conversations", status_code=status.HTTP_201_CREATED)
    def create_conversation(
        payload: AiChatConversationInput, request: Request
    ) -> dict[str, object]:
        try:
            return request.app.state.chat_service.conversation(
                symbol=payload.symbol,
                timeframe=payload.timeframe,
                source_run_id=payload.source_run_id,
                context_kind=payload.context_kind,
                context_id=payload.context_id,
                force_new=payload.force_new,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/ai/conversations")
    def list_conversations(
        request: Request, symbol: str | None = None, timeframe: str = "daily",
        source_run_id: str | None = None,
        context_kind: str = "trend_analysis", context_id: str | None = None,
        include_archived: bool = Query(default=True),
    ) -> dict[str, object]:
        try:
            items = request.app.state.chat_service.list_conversations(
                symbol=symbol, timeframe=timeframe, source_run_id=source_run_id,
                context_kind=context_kind, context_id=context_id,
                include_archived=include_archived,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"items": items}

    @router.get("/api/ai/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request) -> dict[str, object]:
        result = request.app.state.chat_service.get_conversation(conversation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="chat conversation not found")
        return result

    @router.patch("/api/ai/conversations/{conversation_id}")
    def update_conversation(
        conversation_id: str, payload: AiChatConversationUpdateInput, request: Request
    ) -> dict[str, object]:
        try:
            result = request.app.state.chat_service.update_conversation(
                conversation_id, title=payload.title, status=payload.status
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if result is None:
            raise HTTPException(status_code=404, detail="chat conversation not found")
        return result

    @router.delete("/api/ai/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: str, request: Request) -> Response:
        try:
            deleted = request.app.state.chat_service.delete_conversation(conversation_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not deleted:
            raise HTTPException(status_code=404, detail="chat conversation not found")
        return Response(status_code=204)

    @router.post(
        "/api/ai/conversations/{conversation_id}/turns",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def start_turn(
        conversation_id: str, payload: AiChatTurnInput, request: Request
    ) -> dict[str, object]:
        try:
            return request.app.state.chat_service.start_turn(
                conversation_id=conversation_id,
                content=payload.content,
                template_id=payload.template_id,
                user_inputs={
                    key: value.model_dump(mode="json")
                    for key, value in {
                        "position": payload.position,
                        "risk_reward": payload.risk_reward,
                    }.items() if value is not None
                },
                selected_signal_item_ids=payload.selected_signal_item_ids,
                selected_signal_run_id=payload.selected_signal_run_id,
                learning_asset_path=payload.learning_asset_path,
                learning_page_title=payload.learning_page_title,
            )
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/ai/turns/{turn_id}/events")
    def stream_turn(
        turn_id: str,
        request: Request,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            events = request.app.state.chat_service.events(turn_id, last_event_id or 0)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return StreamingResponse(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/ai/turns/{turn_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel_turn(turn_id: str, request: Request) -> dict[str, str]:
        try:
            request.app.state.chat_service.cancel(turn_id)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"status": "cancelling"}

    @router.get("/api/ai/turns/{turn_id}/context")
    def turn_context(turn_id: str, request: Request) -> dict[str, object]:
        result = request.app.state.chat_service.turn_context_summary(turn_id)
        if result is None:
            raise HTTPException(status_code=404, detail="chat turn context not found")
        return result

    @router.post("/api/ai/turns/{turn_id}/retry", status_code=status.HTTP_202_ACCEPTED)
    def retry_turn(turn_id: str, request: Request) -> dict[str, object]:
        try:
            return request.app.state.chat_service.retry_turn(turn_id)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router
