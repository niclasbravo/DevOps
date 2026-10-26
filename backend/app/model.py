"""Modelo de recomendación por contenido (TF-IDF + categorías, k-NN con similitud de Tanimoto).

Cada juego es un vector que une sus categorías codificadas como 0/1 (géneros,
temas, modos y keywords) con el TF-IDF de su resumen. La similitud es la de
Tanimoto, x·p / (|x|² + |p|² − x·p): la generalización del índice de Jaccard a
vectores con pesos. En la evaluación superó tanto al coseno como a Jaccard
(ver README, sección 6).

El modelo se entrena fuera de línea con `python -m ml.train` y se guarda como un
archivo joblib junto a `model_meta.json`, que trae su hash SHA-256. Antes de
cargarlo se verifica ese hash: un pickle puede ejecutar código al abrirse, así
que solo se carga el archivo que generó nuestro pipeline.
"""

import hashlib
import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.preprocessing import normalize

logger = logging.getLogger("gamematch.model")

# Grupos de categorías que se codifican como 0/1 (además del texto).
CATEGORY_GROUPS = ("genres", "themes", "game_modes", "keywords")

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "gamematch.joblib"
META_FILENAME = "model_meta.json"


class ModelIntegrityError(RuntimeError):
    """El archivo del modelo no coincide con el hash registrado en model_meta.json."""


def normalize_title(title: str) -> str:
    """Título en minúsculas y con espacios simples, para buscar sin importar el formato."""
    return " ".join(title.lower().split())


def normalize_label(label: str) -> str:
    """Etiqueta comparable: minúsculas, sin guiones ni guiones bajos."""
    return " ".join(re.sub(r"[-_]", " ", label.lower()).split())


def label_aliases(label: str) -> set[str]:
    """Formas en que un usuario puede escribir una etiqueta.

    "Role-playing (RPG)" se reconoce como "role playing (rpg)", "role playing" y "rpg".
    """
    norm = normalize_label(label)
    aliases = {norm}
    inner = re.findall(r"\(([^)]+)\)", norm)
    aliases.update(i.strip() for i in inner)
    outer = re.sub(r"\([^)]*\)", "", norm).strip()
    if outer:
        aliases.add(outer)
    return aliases


def combine_blocks(blocks: dict[str, sparse.spmatrix], weights: dict[str, float]) -> sparse.csr_matrix:
    """Une los bloques (texto y cada grupo de categorías) en un solo vector por fila.

    Las categorías quedan como 0/1 multiplicadas por la raíz de su peso (con peso 1
    y sin texto, Tanimoto es exactamente Jaccard). El texto se normaliza para que
    todos los resúmenes pesen lo mismo sin importar su largo, y se escala por la
    raíz de su peso.
    """
    parts = []
    for name, block in blocks.items():
        block = normalize(block) if name == "text" else block
        parts.append(block * np.sqrt(weights[name]))
    return sparse.hstack(parts, format="csr").astype(np.float32)


def tanimoto(matrix: sparse.csr_matrix, sq_norms: np.ndarray, profile: sparse.csr_matrix) -> np.ndarray:
    """Similitud de Tanimoto de cada fila de `matrix` contra cada fila de `profile` (n_juegos × n_perfiles)."""
    dot = (matrix @ profile.T).toarray()
    p_sq = np.asarray(profile.multiply(profile).sum(axis=1)).ravel()
    denom = sq_norms[:, None] + p_sq[None, :] - dot
    return np.divide(dot, denom, out=np.zeros_like(dot), where=denom > 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_meta(model_path: Path) -> dict:
    return json.loads((model_path.parent / META_FILENAME).read_text(encoding="utf-8"))


def verify_model_file(model_path: Path | None = None) -> dict:
    """Comprueba que el SHA-256 del modelo coincide con model_meta.json y devuelve la metadata."""
    model_path = Path(model_path or DEFAULT_MODEL_PATH)
    meta = read_meta(model_path)
    actual = sha256_file(model_path)
    if actual != meta.get("sha256"):
        raise ModelIntegrityError(f"SHA-256 de {model_path.name} no coincide: {actual} != {meta.get('sha256')}")
    return meta


class ContentModel:
    """Recomendador por contenido sobre un bundle generado por `ml.features.fit_bundle`."""

    def __init__(self, bundle: dict, meta: dict | None = None):
        self.vectorizer = bundle["vectorizer"]
        self.binarizers = bundle["binarizers"]
        self.weights = bundle["weights"]
        self.matrix: sparse.csr_matrix = bundle["matrix"]
        self.games: list[dict] = bundle["games"]
        self.popularity_penalty: float = bundle.get("popularity_penalty", 0.0)
        self.meta = meta or {}
        self.sq_norms = np.asarray(self.matrix.multiply(self.matrix).sum(axis=1)).ravel()

        popularity = np.log1p(np.array([g.get("rating_count") or 0 for g in self.games], dtype=np.float32))
        self.popularity = popularity / popularity.max() if popularity.max() > 0 else popularity

        # Si dos juegos comparten título, gana el más valorado (rating_count más alto).
        self._by_title: dict[str, int] = {}
        for i, game in enumerate(self.games):
            key = normalize_title(game["title"])
            best = self._by_title.get(key)
            if best is None or (game.get("rating_count") or 0) > (self.games[best].get("rating_count") or 0):
                self._by_title[key] = i

        # Índice etiqueta → (grupo, columna) para traducir los tags que escribe el usuario.
        self._label_index: dict[str, list[tuple[str, int]]] = {}
        for group, binarizer in self.binarizers.items():
            for col, label in enumerate(binarizer.classes_):
                for alias in label_aliases(label):
                    self._label_index.setdefault(alias, []).append((group, col))

    @property
    def size(self) -> int:
        return len(self.games)

    def find(self, title: str) -> int | None:
        return self._by_title.get(normalize_title(title))

    def tags_vector(self, tags: list[str]) -> sparse.csr_matrix | None:
        """Vector de los tags de interés en el mismo espacio que los juegos, o None si ninguno se reconoce."""
        clean = [normalize_label(t) for t in tags if t and t.strip()]
        if not clean:
            return None
        blocks = {"text": self.vectorizer.transform([" ".join(clean)])}
        matched = False
        for group, binarizer in self.binarizers.items():
            row = np.zeros((1, len(binarizer.classes_)), dtype=np.float32)
            for tag in clean:
                for g, col in self._label_index.get(tag, []):
                    if g == group:
                        row[0, col] = 1.0
                        matched = True
            blocks[group] = sparse.csr_matrix(row)
        if not matched and blocks["text"].nnz == 0:
            return None
        return combine_blocks(blocks, self.weights)

    def profile(self, liked_idx: list[int], tags: list[str]) -> sparse.csr_matrix | None:
        """Perfil del usuario: promedio de sus juegos favoritos más el vector de sus tags."""
        parts = []
        if liked_idx:
            parts.append(sparse.csr_matrix(self.matrix[liked_idx].mean(axis=0)))
        tag_vec = self.tags_vector(tags)
        if tag_vec is not None:
            parts.append(tag_vec)
        if not parts:
            return None
        total = parts[0] if len(parts) == 1 else parts[0] + parts[1]
        return total if total.nnz else None

    def scores(self, profiles: sparse.csr_matrix) -> np.ndarray:
        """Similitud de todo el catálogo contra uno o más perfiles (k-NN por fuerza bruta), con la
        penalización de popularidad aplicada. Devuelve una matriz n_juegos × n_perfiles."""
        sims = tanimoto(self.matrix, self.sq_norms, profiles)
        if self.popularity_penalty:
            sims *= (1.0 - self.popularity_penalty * self.popularity)[:, None]
        return sims

    def top_indices(self, liked_idx: list[int], tags: list[str], k: int) -> list[tuple[int, float]]:
        """Los k vecinos más cercanos al perfil, sin repetir los juegos que el usuario ya indicó."""
        profile = self.profile(liked_idx, tags)
        if profile is None:
            raise ValueError("Se necesita al menos un juego conocido o un tag de interés")
        sims = self.scores(profile)[:, 0]
        sims[liked_idx] = -np.inf
        k = min(k, len(sims) - len(set(liked_idx)))
        if k <= 0:
            return []
        top = np.argpartition(-sims, k - 1)[:k]
        top = top[np.lexsort((top, -sims[top]))]
        return [(int(i), float(sims[i])) for i in top if sims[i] > 0]

    def recommend(self, liked_games: list[str], tags: list[str], limit: int) -> list[dict]:
        liked_idx = sorted({i for i in (self.find(t) for t in liked_games) if i is not None})
        top = self.top_indices(liked_idx, tags, limit)
        return [{"title": self.games[i]["title"], "score": round(s, 3)} for i, s in top]

    def list_games(self, limit: int, offset: int) -> list[dict]:
        out = []
        for game in self.games[offset : offset + limit]:
            tags = set(game.get("genres", [])) | set(game.get("themes", [])) | set(game.get("game_modes", []))
            out.append({"title": game["title"], "tags": sorted(tags)})
        return out


def load_model(model_path: Path | None = None) -> ContentModel | None:
    """Carga el modelo verificando su SHA-256. Devuelve None si no existe o no es confiable."""
    import joblib

    model_path = Path(model_path or os.environ.get("MODEL_PATH") or DEFAULT_MODEL_PATH)
    if not model_path.exists():
        logger.warning("No hay modelo en %s; se usa el recomendador Jaccard", model_path)
        return None
    try:
        meta = verify_model_file(model_path)
    except (FileNotFoundError, json.JSONDecodeError, ModelIntegrityError) as exc:
        logger.error("Modelo rechazado (%s); se usa el recomendador Jaccard", exc)
        return None
    # joblib usa pickle: solo llegamos aquí si el hash coincide con el que registró nuestro pipeline,
    # y model_meta.json entra al repo solo por PR revisado.
    model = ContentModel(joblib.load(model_path), meta)
    logger.info("Modelo cargado: %d juegos, commit %s", model.size, meta.get("commit", "?"))
    return model


@lru_cache(maxsize=1)
def get_model() -> ContentModel | None:
    """Modelo compartido por la API (se carga una sola vez)."""
    return load_model()
