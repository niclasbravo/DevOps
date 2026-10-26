"""Pasos 3 y 4: vectores de los juegos (TF-IDF + categorías 0/1) e índice de vecinos.

El resultado es un "bundle" (diccionario) que la API carga con `app.model.ContentModel`.
"""

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import MultiLabelBinarizer

from app.model import CATEGORY_GROUPS, ContentModel, combine_blocks, normalize_label

DEFAULT_WEIGHTS = {"text": 1.0, "genres": 1.0, "themes": 1.0, "game_modes": 1.0, "keywords": 1.0}


def make_weights(text: float, keywords: float = 1.0) -> dict[str, float]:
    """Pesos por bloque. Cada categoría compartida suma 1 (× su peso); el texto suma hasta `text`."""
    return {"text": text, "genres": 1.0, "themes": 1.0, "game_modes": 1.0, "keywords": keywords}


def fit_blocks(df: pd.DataFrame) -> dict:
    """Entrena el TF-IDF y los codificadores 0/1. Los pesos se aplican después (`assemble`)."""
    vectorizer = TfidfVectorizer(
        stop_words="english",
        min_df=2,
        max_df=0.5,
        max_features=20000,
        sublinear_tf=True,
        ngram_range=(1, 2),
    )
    blocks = {"text": vectorizer.fit_transform(df["text"].tolist())}
    binarizers = {}
    for group in CATEGORY_GROUPS:
        binarizer = MultiLabelBinarizer(sparse_output=True)
        blocks[group] = binarizer.fit_transform(df[group].tolist()).astype(np.float32)
        binarizers[group] = binarizer

    games = [
        {
            "id": int(row.id),
            "title": row.name,
            "year": None if pd.isna(row.year) else int(row.year),
            "genres": list(row.genres),
            "themes": list(row.themes),
            "game_modes": list(row.game_modes),
            "keywords": list(row.keywords),
            "rating_count": int(row.rating_count),
        }
        for row in df.itertuples(index=False)
    ]
    return {"vectorizer": vectorizer, "binarizers": binarizers, "blocks": blocks, "games": games}


def assemble(fitted: dict, weights: dict | None = None, popularity_penalty: float = 0.0) -> dict:
    """Arma el bundle del modelo (lo que se guarda con joblib) a partir de los bloques entrenados."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    return {
        "format": 1,
        "vectorizer": fitted["vectorizer"],
        "binarizers": fitted["binarizers"],
        "weights": weights,
        "matrix": combine_blocks(fitted["blocks"], weights),
        "games": fitted["games"],
        "popularity_penalty": popularity_penalty,
    }


def fit_bundle(df: pd.DataFrame, weights: dict | None = None, popularity_penalty: float = 0.0) -> dict:
    return assemble(fit_blocks(df), weights, popularity_penalty)


def fit_model(df: pd.DataFrame, weights: dict | None = None, popularity_penalty: float = 0.0) -> ContentModel:
    return ContentModel(fit_bundle(df, weights, popularity_penalty))


def jaccard_matrix(df: pd.DataFrame) -> tuple[sparse.csr_matrix, dict[str, int]]:
    """Matriz binaria juego × etiqueta para el baseline Jaccard (géneros, temas, modos y keywords)."""
    vocab: dict[str, int] = {}
    rows, cols = [], []
    for i, row in enumerate(df.itertuples(index=False)):
        labels = {normalize_label(lbl) for group in CATEGORY_GROUPS for lbl in getattr(row, group)}
        for label in labels:
            rows.append(i)
            cols.append(vocab.setdefault(label, len(vocab)))
    data = np.ones(len(rows), dtype=np.float32)
    return sparse.csr_matrix((data, (rows, cols)), shape=(len(df), len(vocab))), vocab
