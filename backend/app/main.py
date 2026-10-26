"""API de GameMatch."""

import datetime as dt
import json
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.catalog import CATALOG, find_game
from app.model import get_model
from app.recommender import active_recommender, recommend


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_model()  # carga (y verifica) el modelo al arrancar, no en la primera consulta
    yield


app = FastAPI(title="GameMatch", description="Recomendador de videojuegos indie", version="0.2.0", lifespan=lifespan)

# Últimas latencias de /recommendations (ms), para medir el p95 en producción.
_latencies: deque[float] = deque(maxlen=1000)
_feedback_lock = threading.Lock()


class RecommendationRequest(BaseModel):
    liked_games: list[str] = Field(default_factory=list, examples=[["Hollow Knight"]])
    tags: list[str] = Field(default_factory=list, examples=[["roguelike"]])
    limit: int = Field(default=5, ge=1, le=20)


class Recommendation(BaseModel):
    title: str
    score: float


class Feedback(BaseModel):
    # Solo un id anónimo, el juego y el voto: nada que identifique a la persona.
    user_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$", examples=["a1b2c3"])
    game: str = Field(min_length=1, max_length=200, examples=["Hades"])
    vote: Literal[1, -1]


@app.middleware("http")
async def measure_latency(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
    if request.url.path == "/recommendations":
        _latencies.append(elapsed_ms)
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/games")
def list_games(limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> list[dict]:
    model = get_model()
    if model is not None:
        return model.list_games(limit, offset)
    return [{"title": g["title"], "tags": sorted(g["tags"])} for g in CATALOG[offset : offset + limit]]


@app.post("/recommendations", response_model=list[Recommendation])
def get_recommendations(req: RecommendationRequest) -> list[dict]:
    try:
        return recommend(req.liked_games, req.tags, req.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/feedback", status_code=201)
def post_feedback(fb: Feedback) -> dict:
    model = get_model()
    known = model.find(fb.game) is not None if model is not None else find_game(fb.game) is not None
    if not known:
        raise HTTPException(status_code=404, detail=f"Juego desconocido: {fb.game}")
    path = Path(os.environ.get("FEEDBACK_PATH", "data/feedback.jsonl"))
    record = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **fb.model_dump()}
    with _feedback_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"status": "ok"}


@app.get("/stats")
def stats() -> dict:
    model = get_model()
    latencies = list(_latencies)
    info = {
        "recommender": active_recommender(),
        "catalog_size": model.size if model is not None else len(CATALOG),
        "latency_ms": {
            "requests": len(latencies),
            "p50": round(float(np.percentile(latencies, 50)), 1) if latencies else None,
            "p95": round(float(np.percentile(latencies, 95)), 1) if latencies else None,
        },
    }
    if model is not None:
        info["model"] = {k: model.meta.get(k) for k in ("commit", "sha256", "trained_at", "data_file")}
    return info
