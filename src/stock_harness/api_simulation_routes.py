"""Isolated scenario-lab routes; no market store access or order side effects."""

from fastapi import APIRouter, HTTPException

from stock_harness.trade_simulation import SimulationInput, example_scenarios, simulate


def create_simulation_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/trade-simulation/scenarios")
    def scenarios():
        return {"items": example_scenarios()}

    @router.post("/api/trade-simulation/run")
    def run(payload: SimulationInput):
        try:
            return simulate(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
