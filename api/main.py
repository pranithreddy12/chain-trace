"""FastAPI layer over the existing ChainTrace backend.

New code only — the src/ library and app/streamlit_app.py are untouched.
"""

from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from starlette.middleware.cors import CORSMiddleware

from src.application.investigation_service import InvestigationService
from src.application.report_service import ReportService
from src.domain.enums import Chain
from src.config.settings import get_settings

from .payload import (
    build_payload,
    endpoints_from_result,
    patterns_from_result,
    stats_from_result,
    transactions_from_result,
    warnings_from_result,
)

settings = get_settings()
svc = InvestigationService()

# In-memory cache: investigation_id -> InvestigationResult
_RESULT_CACHE: dict[str, object] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="ChainTrace API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class TraceRequest(BaseModel):
    address: str = Field(..., min_length=1)
    chain: str = Field(..., pattern="^(ethereum|bsc|tron)$")
    max_depth: int = Field(default=2, ge=1, le=6)
    max_branches: int = Field(default=25, ge=1, le=100)
    token_filter: str | None = Field(default=None, max_length=66)


class HighlightRequest(BaseModel):
    investigation_id: str = Field(..., min_length=1)


class TraceResponse(BaseModel):
    investigation_id: str
    seed: dict
    stats: dict
    nodes: list
    links: list
    endpoints: list
    transactions: list
    patterns: dict
    warnings: list


# ---------------------------------------------------------------------------
# POST /api/trace
# ---------------------------------------------------------------------------

@app.post("/api/trace", response_model=TraceResponse)
async def trace(req: TraceRequest):
    try:
        chain = Chain(req.chain)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown chain: {req.chain}")

    try:
        result = await svc.run_investigation(
            seed_address=req.address,
            chain=chain,
            max_depth=req.max_depth,
            max_branches=req.max_branches,
            token_filter=req.token_filter,
        )
    except Exception as exc:
        msg = str(exc)
        lowered = msg.lower()
        if "invalid" in lowered or "not found" in lowered or "no transactions found" in lowered:
            raise HTTPException(status_code=400, detail=msg) from exc
        raise HTTPException(status_code=500, detail=msg) from exc

    _RESULT_CACHE[result.investigation.investigation_id] = result
    return _trace_response_from_result(result)


# ---------------------------------------------------------------------------
# GET /api/report/{investigation_id}
# ---------------------------------------------------------------------------

@app.get("/api/report/{investigation_id}")
async def report(investigation_id: str, format: str = "text"):
    result = _RESULT_CACHE.get(investigation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Investigation not found")

    if format == "json":
        return {"report": ReportService.export_json(result)}
    return {"report": ReportService.generate_detailed_report(result)}


# ---------------------------------------------------------------------------
# POST /api/highlight
# ---------------------------------------------------------------------------

@app.post("/api/highlight")
async def highlight(req: HighlightRequest):
    result = _RESULT_CACHE.get(req.investigation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Investigation not found")

    paths = result.graph.get_paths_to_endpoints(max_paths=1)
    highlighted_path = paths[0] if paths else []

    payload = build_payload(result, highlighted_path)

    seed_addr = result.seed_address
    return TraceResponse(
        investigation_id=result.investigation.investigation_id,
        seed={
            "address": seed_addr.address,
            "chain": seed_addr.chain.value,
            "label": seed_addr.label,
        },
        stats=stats_from_result(result),
        nodes=payload["nodes"],
        links=payload["links"],
        endpoints=endpoints_from_result(result),
        transactions=transactions_from_result(result),
        patterns=patterns_from_result(result),
        warnings=warnings_from_result(result),
    )
