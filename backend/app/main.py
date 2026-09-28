"""API de GameMatch."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.catalog import CATALOG
from app.recommender import recommend

app = FastAPI(title="GameMatch", description="Recomendador de videojuegos indie", version="0.1.0")


class RecommendationRequest(BaseModel):
    liked_games: list[str] = Field(default_factory=list, examples=[["Hollow Knight"]])
    tags: list[str] = Field(default_factory=list, examples=[["roguelike"]])
    limit: int = Field(default=5, ge=1, le=20)


class Recommendation(BaseModel):
    title: str
    score: float


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/games")
def list_games() -> list[dict]:
    return [{"title": g["title"], "tags": sorted(g["tags"])} for g in CATALOG]


@app.post("/recommendations", response_model=list[Recommendation])
def get_recommendations(req: RecommendationRequest) -> list[dict]:
    try:
        return recommend(req.liked_games, req.tags, req.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
