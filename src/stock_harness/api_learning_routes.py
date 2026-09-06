"""Trading-system learning catalog and allowlisted static assets."""

from __future__ import annotations

from collections.abc import Callable
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from stock_harness.learning_library import LearningLibrary, LearningLibraryError


LOGGER = logging.getLogger(__name__)


def create_learning_router(
    library: LearningLibrary,
    browser_opener: Callable[[str], bool],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/learning/systems")
    def list_learning_systems() -> dict[str, object]:
        try:
            return {"items": library.list_systems()}
        except (LearningLibraryError, OSError, ValueError) as error:
            LOGGER.warning("learning_catalog_unavailable error=%s", _bounded(error))
            raise HTTPException(status_code=503, detail="trading-system library unavailable") from error

    @router.post("/api/learning/systems/{system_id}/open")
    def open_learning_system(system_id: str, request: Request) -> dict[str, object]:
        try:
            system = library.get_system(system_id)
            if system is None:
                raise HTTPException(status_code=404, detail="trading-system course not found")
            route = "/learning/" + str(system["index_path"])
            url = str(request.base_url).rstrip("/") + route
            if not browser_opener(url):
                raise LearningLibraryError("system browser rejected the course URL")
            LOGGER.info("learning_course_opened system_id=%s", system_id)
            return {"status": "opened", "system_id": system_id, "url": route}
        except HTTPException:
            raise
        except (LearningLibraryError, OSError, ValueError) as error:
            LOGGER.warning(
                "learning_course_open_failed system_id=%s error=%s",
                system_id, _bounded(error),
            )
            raise HTTPException(status_code=503, detail="could not open trading-system course") from error

    @router.get("/learning/{asset_path:path}", include_in_schema=False)
    def learning_asset(asset_path: str) -> FileResponse:
        try:
            return FileResponse(library.resolve_public_file(asset_path))
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="learning asset not found") from error
        except LearningLibraryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return router


def _bounded(error: Exception) -> str:
    return " ".join(str(error).split())[:300] or type(error).__name__
