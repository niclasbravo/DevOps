"""API de GameMatch."""

import mimetypes
import sqlite3
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.admin import router as admin_router
from app.auth import require_permission
from app.auth import router as auth_router
from app.catalog import CATALOG
from app.crypto import pseudonymize
from app.db import init_db, save_vote
from app.errors import register_error_handlers
from app.logging_safe import configure_logging
from app.me import router as me_router
from app.model import get_model
from app.recommender import active_recommender, canonical_title, recommend
from app.reports import router as reports_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    get_model()  # carga (y verifica) el modelo al arrancar, no en la primera consulta
    init_db()
    yield


app = FastAPI(title="GameMatch", description="Recomendador de videojuegos indie", version="0.2.0", lifespan=lifespan)

# Últimas latencias de /recommendations (ms), para medir el p95 en producción.
_latencies: deque[float] = deque(maxlen=1000)

register_error_handlers(app)
app.include_router(auth_router)
app.include_router(me_router)
app.include_router(admin_router)
app.include_router(reports_router)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_HTML = STATIC_DIR / "index.html"
# Hoja de estilos, scripts y fuentes de la página, auto-alojados. Lo externo son las portadas, que el
# navegador pide a images.igdb.com, y la librería de animación del titular (GSAP, desde cdn.jsdelivr.net).
# Windows y las imágenes slim no siempre conocen .woff2: sin esto, las fuentes salen como octet-stream.
mimetypes.add_type("font/woff2", ".woff2")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # un campo no previsto se rechaza: no llega a procesarse

    liked_games: list[str] = Field(default_factory=list, examples=[["Hollow Knight"]])
    tags: list[str] = Field(default_factory=list, examples=[["roguelike"]])
    limit: int = Field(default=5, ge=1, le=20)


class Recommendation(BaseModel):
    title: str
    score: float
    year: int | None = None
    genres: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list, description="Etiquetas que el juego comparte con tu perfil")
    cover_id: str | None = Field(default=None, description="image_id de la portada en IGDB")
    url: str | None = Field(default=None, description="Ficha del juego en IGDB")
    store_url: str | None = Field(default=None, description="Página del juego en Steam")
    price: int | None = Field(default=None, description="Precio base en Steam, en la moneda que informa /stats")


class Feedback(BaseModel):
    # La identidad sale del token, nunca del cuerpo de la petición.
    model_config = ConfigDict(extra="forbid")

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


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Página web mínima que consume la API."""
    return FileResponse(INDEX_HTML, media_type="text/html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/games")
def list_games(limit: int = Query(default=50, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> list[dict]:
    model = get_model()
    if model is not None:
        return model.list_games(limit, offset)
    return [
        {"title": g["title"], "tags": sorted(g["tags"]), "year": None, "cover_id": None}
        for g in CATALOG[offset : offset + limit]
    ]


@app.post("/recommendations", response_model=list[Recommendation])
def get_recommendations(req: RecommendationRequest) -> list[dict]:
    try:
        return recommend(req.liked_games, req.tags, req.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/feedback", status_code=201)
def post_feedback(fb: Feedback, account: sqlite3.Row = Depends(require_permission("feedback:write"))) -> dict:
    title = canonical_title(fb.game)  # el título del catálogo: "hades" y "Hades" son el mismo voto
    if title is None:
        raise HTTPException(status_code=404, detail=f"Juego desconocido: {fb.game}")
    save_vote(pseudonymize(account["id"]), title, fb.vote)
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
        # De cuándo y en qué moneda son los precios que acompañan a las recomendaciones.
        "prices": {
            "currency": model.media.currency if model is not None else None,
            "as_of": model.media.fetched_on if model is not None else None,
        },
    }
    return info
