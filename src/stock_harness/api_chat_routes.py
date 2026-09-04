"""Result-bound Codex chat routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from stock_harness.api_models import AiChatConversationInput, AiChatTurnInput


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
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/ai/conversations/{conversation_id}")
    def get_conversation(conversation_id: str, request: Request) -> dict[str, object]:
        result = request.app.state.chat_service.get_conversation(conversation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="chat conversation not found")
        return result

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

    return router
