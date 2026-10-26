"""Paso 5: evaluación del modelo contra el baseline Jaccard.

Métricas de ranking (el usuario solo mira los primeros resultados):
- Precision@10: de los 10 recomendados, cuántos son relevantes.
- nDCG@10: si los relevantes aparecen arriba en la lista.
- Cobertura: qué parte del catálogo llega a recomendarse alguna vez.
- Latencia p95 de una recomendación.

Referencias: el campo similar_games de IGDB y las consultas armadas por el equipo (eval_queries.json).
"""

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from app.model import ContentModel, normalize_label, normalize_title
from ml.features import jaccard_matrix

K = 10


@dataclass
class Query:
    """Una consulta de evaluación: juegos que gustan + tags, y los juegos que esperamos ver (índices)."""

    liked: list[int]
    tags: list[str]
    relevant: set[int]
    source: str = "similar_games"
    label: str = ""
    missing: list[str] = field(default_factory=list)


def precision_at_k(ranked: list[int], relevant: set[int], k: int = K) -> float:
    return sum(1 for i in ranked[:k] if i in relevant) / k


def ndcg_at_k(ranked: list[int], relevant: set[int], k: int = K) -> float:
    dcg = sum(1 / math.log2(pos + 2) for pos, i in enumerate(ranked[:k]) if i in relevant)
    ideal = sum(1 / math.log2(pos + 2) for pos in range(min(len(relevant), k)))
    return dcg / ideal if ideal else 0.0


class JaccardBaseline:
    """El recomendador actual (índice de Jaccard sobre etiquetas), aplicado al mismo catálogo que el modelo."""

    def __init__(self, df: pd.DataFrame):
        self.matrix, self.vocab = jaccard_matrix(df)
        self.sizes = np.asarray(self.matrix.sum(axis=1)).ravel()

    def top_indices(self, liked: list[int], tags: list[str], k: int) -> list[int]:
        profile = np.zeros(self.matrix.shape[1], dtype=np.float32)
        if liked:
            profile[np.asarray(self.matrix[liked].sum(axis=0)).ravel() > 0] = 1.0
        for tag in tags:
            col = self.vocab.get(normalize_label(tag))
            if col is not None:
                profile[col] = 1.0
        if not profile.any():
            return []
        inter = self.matrix @ profile
        union = self.sizes + profile.sum() - inter
        sims = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        sims[liked] = -1.0
        order = np.lexsort((np.arange(len(sims)), -sims))[:k]
        return [int(i) for i in order if sims[i] > 0]


def similar_games_queries(df: pd.DataFrame, max_queries: int | None = None, seed: int = 42) -> list[Query]:
    """Una consulta por juego: le gusta ese juego y los relevantes son sus similar_games presentes en el catálogo."""
    pos = {int(gid): i for i, gid in enumerate(df["id"])}
    queries = []
    for i, similar in enumerate(df["similar_games"]):
        relevant = {pos[s] for s in similar if s in pos and pos[s] != i}
        if relevant:
            queries.append(Query(liked=[i], tags=[], relevant=relevant))
    if max_queries and len(queries) > max_queries:
        rng = np.random.default_rng(seed)
        keep = sorted(rng.choice(len(queries), size=max_queries, replace=False))
        queries = [queries[j] for j in keep]
    return queries


def load_team_queries(path: Path, df: pd.DataFrame) -> list[Query]:
    """Consultas del equipo. Los títulos que no estén en el catálogo se ignoran (y se informan)."""
    if not Path(path).exists():
        return []
    by_title: dict[str, int] = {}
    for i, (name, rc) in enumerate(zip(df["name"], df["rating_count"])):
        key = normalize_title(name)
        if key not in by_title or rc > df["rating_count"].iloc[by_title[key]]:
            by_title[key] = i
    queries = []
    for item in json.loads(Path(path).read_text(encoding="utf-8")):
        liked = [by_title[normalize_title(t)] for t in item.get("liked_games", []) if normalize_title(t) in by_title]
        expected = [by_title[normalize_title(t)] for t in item["expected"] if normalize_title(t) in by_title]
        missing = [t for t in item.get("liked_games", []) + item["expected"] if normalize_title(t) not in by_title]
        relevant = set(expected) - set(liked)
        if (liked or item.get("tags")) and relevant:
            label = item.get("name", "")
            queries.append(Query(liked, item.get("tags", []), relevant, source="equipo", label=label, missing=missing))
    return queries


def evaluate_ranker(rank_fn, queries: list[Query], k: int = K) -> dict:
    """P@k y nDCG@k promedio de una función rank_fn(liked, tags, k) -> lista de índices."""
    if not queries:
        return {"precision@10": 0.0, "ndcg@10": 0.0, "queries": 0}
    p, n = [], []
    for q in queries:
        ranked = rank_fn(q.liked, q.tags, k)
        p.append(precision_at_k(ranked, q.relevant, k))
        n.append(ndcg_at_k(ranked, q.relevant, k))
    return {"precision@10": float(np.mean(p)), "ndcg@10": float(np.mean(n)), "queries": len(queries)}


def model_ranker(model: ContentModel):
    def rank(liked, tags, k):
        try:
            return [i for i, _ in model.top_indices(liked, tags, k)]
        except ValueError:
            return []

    return rank


def coverage(model: ContentModel, k: int = K, batch: int = 512) -> float:
    """Fracción del catálogo que aparece en el top-k de al menos un juego usado como consulta."""
    n = model.size
    seen = np.zeros(n, dtype=bool)
    for start in range(0, n, batch):
        block = model.scores(model.matrix[start : start + batch]).T  # perfiles × catálogo
        rows = np.arange(block.shape[0])
        block[rows, rows + start] = -np.inf
        kk = min(k, n - 1)
        top = np.argpartition(-block, kk - 1, axis=1)[:, :kk]
        valid = np.take_along_axis(block, top, axis=1) > 0
        seen[top[valid]] = True
    return float(seen.mean())


def jaccard_coverage(baseline: JaccardBaseline, k: int = K, batch: int = 512) -> float:
    n = baseline.matrix.shape[0]
    seen = np.zeros(n, dtype=bool)
    for start in range(0, n, batch):
        inter = (baseline.matrix[start : start + batch] @ baseline.matrix.T).toarray()
        union = baseline.sizes[start : start + batch, None] + baseline.sizes[None, :] - inter
        sims = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        rows = np.arange(sims.shape[0])
        sims[rows, rows + start] = -1.0
        kk = min(k, n - 1)
        top = np.argpartition(-sims, kk - 1, axis=1)[:, :kk]
        valid = np.take_along_axis(sims, top, axis=1) > 0
        seen[top[valid]] = True
    return float(seen.mean())


def latency_p95_ms(model: ContentModel, queries: list[Query], limit: int = K, max_queries: int = 300) -> float:
    """Latencia p95 de `model.recommend` con títulos reales (incluye la búsqueda por título)."""
    times = []
    for q in queries[:max_queries]:
        titles = [model.games[i]["title"] for i in q.liked]
        start = time.perf_counter()
        try:
            model.recommend(titles, q.tags, limit)
        except ValueError:
            pass
        times.append((time.perf_counter() - start) * 1000)
    return float(np.percentile(times, 95)) if times else 0.0
