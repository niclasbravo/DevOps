"""Pasos 2 a 6: limpiar, vectorizar, evaluar contra Jaccard y guardar el modelo.

Uso:
    python -m ml.train                          # usa el CSV más reciente de data/raw/
    python -m ml.train --data data/raw/igdb_2026-10-26.csv

El script termina con código 1 (y no guarda el modelo) si el modelo no supera a
Jaccard en Precision@10 y nDCG@10, si la cobertura queda bajo 40 % o si la
latencia p95 llega a 200 ms. Siempre escribe data/metrics.json y data/metrics.md.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn

from app.model import DEFAULT_MODEL_PATH, META_FILENAME, ContentModel, sha256_file
from ml.clean import DataValidationError, clean_games
from ml.evaluate import (
    JaccardBaseline,
    coverage,
    evaluate_ranker,
    jaccard_coverage,
    latency_p95_ms,
    load_team_queries,
    model_ranker,
    similar_games_queries,
)
from ml.features import assemble, fit_blocks, make_weights

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BACKEND_DIR / "data" / "raw"
QUERIES_PATH = Path(__file__).resolve().parent / "eval_queries.json"

MIN_COVERAGE = 0.40
MAX_P95_MS = 200.0
NDCG_DROP_ALERT = 0.02  # alerta si el nDCG cae más que esto respecto al modelo anterior

TEXT_GRID = (0.5, 1.0, 2.0, 3.0)
KEYWORD_GRID = (0.5, 1.0)
PENALTY_GRID = (0.0, 0.1, 0.2, 0.3, 0.5)


def latest_csv(directory: Path) -> Path:
    files = sorted(directory.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"No hay CSV en {directory}; corre primero `python -m ml.fetch`")
    return files[-1]


def current_commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "desconocido"


def tune(fitted: dict, queries, log) -> tuple[dict, float]:
    """Busca los pesos (texto/keywords) y la penalización de popularidad con mejor nDCG@10.

    Solo se aceptan combinaciones con cobertura ≥ 40 %; la penalización se prueba de menor a mayor.
    """
    best = None
    for text in TEXT_GRID:
        for kw in KEYWORD_GRID:
            weights = make_weights(text, kw)
            model = ContentModel(assemble(fitted, weights))
            ndcg = evaluate_ranker(model_ranker(model), queries)["ndcg@10"]
            log(f"  pesos texto={text:.2f} keywords={kw:.2f} → nDCG@10={ndcg:.4f}")
            if best is None or ndcg > best[0]:
                best = (ndcg, weights)
    weights = best[1]

    chosen = None
    for penalty in PENALTY_GRID:
        model = ContentModel(assemble(fitted, weights, penalty))
        cov = coverage(model)
        ndcg = evaluate_ranker(model_ranker(model), queries)["ndcg@10"]
        log(f"  penalización popularidad={penalty:.1f} → nDCG@10={ndcg:.4f} cobertura={cov:.1%}")
        if cov >= MIN_COVERAGE and (chosen is None or ndcg > chosen[0]):
            chosen = (ndcg, penalty)
    return weights, (chosen[1] if chosen else 0.0)


def metrics_markdown(metrics: dict) -> str:
    m, j = metrics["model"], metrics["jaccard"]
    lines = [
        "## Modelo ML vs Jaccard",
        "",
        f"Datos: `{metrics['data_file']}` · {metrics['n_games']} juegos · commit `{metrics['commit'][:7]}`",
        "",
        "| Referencia | Métrica | Modelo | Jaccard | Meta |",
        "|---|---|---|---|---|",
    ]
    for ref, name in (("similar_games", "similar_games (IGDB)"), ("equipo", "Consultas del equipo")):
        if ref not in m:
            continue
        n = m[ref]["queries"]
        for key, label in (("precision@10", "Precision@10"), ("ndcg@10", "nDCG@10")):
            lines.append(f"| {name} ({n}) | {label} | {m[ref][key]:.3f} | {j[ref][key]:.3f} | Mayor que Jaccard |")
    lines.append(f"| Catálogo | Cobertura | {m['coverage']:.1%} | {j['coverage']:.1%} | ≥ 40 % |")
    lines.append(f"| API | Latencia p95 | {m['latency_p95_ms']:.1f} ms | – | < 200 ms |")
    lines += ["", f"**Resultado:** {'aprobado ✅' if metrics['passed'] else 'rechazado ❌'}"]
    lines += [f"- {r}" for r in metrics["failures"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, help="CSV de ml.fetch (por defecto, el más reciente de data/raw)")
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--queries", type=Path, default=QUERIES_PATH)
    parser.add_argument("--metrics-dir", type=Path, default=BACKEND_DIR / "data")
    parser.add_argument("--max-tune-queries", type=int, default=1500)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows no usa UTF-8 por defecto
    log = print

    data_file = args.data or latest_csv(RAW_DIR)
    log(f"Datos: {data_file}")
    try:
        df, report = clean_games(pd.read_csv(data_file))
    except DataValidationError as exc:
        log(f"::error::Datos inválidos: {exc}")
        return 1
    log(f"Limpieza: {json.dumps(report)}")

    # Mitad de las consultas similar_games para elegir pesos y la otra mitad para medir.
    sim = similar_games_queries(df)
    rng = np.random.default_rng(7)
    order = rng.permutation(len(sim))
    tune_q = [sim[i] for i in order[: len(sim) // 2]][: args.max_tune_queries]
    test_q = [sim[i] for i in order[len(sim) // 2 :]]
    team_q = load_team_queries(args.queries, df)
    log(f"Consultas: {len(tune_q)} para ajuste, {len(test_q)} de prueba (similar_games), {len(team_q)} del equipo")
    for q in team_q:
        if q.missing:
            log(f"  aviso: '{q.label}' tiene títulos fuera del catálogo: {q.missing}")

    fitted = fit_blocks(df)
    log("Ajuste de pesos:")
    weights, penalty = tune(fitted, tune_q, log)
    bundle = assemble(fitted, weights, penalty)
    model = ContentModel(bundle)
    baseline = JaccardBaseline(df)
    jac_rank = baseline.top_indices

    metrics = {
        "data_file": data_file.name,
        "n_games": len(df),
        "commit": current_commit(),
        "weights": weights,
        "popularity_penalty": penalty,
        "model": {"similar_games": evaluate_ranker(model_ranker(model), test_q)},
        "jaccard": {"similar_games": evaluate_ranker(jac_rank, test_q)},
    }
    if team_q:
        metrics["model"]["equipo"] = evaluate_ranker(model_ranker(model), team_q)
        metrics["jaccard"]["equipo"] = evaluate_ranker(jac_rank, team_q)
    metrics["model"]["coverage"] = coverage(model)
    metrics["jaccard"]["coverage"] = jaccard_coverage(baseline)
    metrics["model"]["latency_p95_ms"] = latency_p95_ms(model, test_q + team_q)

    failures = []
    for ref in metrics["model"]:
        if ref in ("coverage", "latency_p95_ms"):
            continue
        for key in ("precision@10", "ndcg@10"):
            if metrics["model"][ref][key] <= metrics["jaccard"][ref][key]:
                failures.append(f"{key} en {ref} no supera a Jaccard")
    if metrics["model"]["coverage"] < MIN_COVERAGE:
        failures.append(f"Cobertura {metrics['model']['coverage']:.1%} < {MIN_COVERAGE:.0%}")
    if metrics["model"]["latency_p95_ms"] >= MAX_P95_MS:
        failures.append(f"Latencia p95 {metrics['model']['latency_p95_ms']:.0f} ms ≥ {MAX_P95_MS:.0f} ms")
    metrics["failures"] = failures
    metrics["passed"] = not failures

    # Alerta si el modelo empeora respecto al que está en producción.
    meta_path = args.model_out.parent / META_FILENAME
    ndcg_drop = False
    if meta_path.exists():
        previous = json.loads(meta_path.read_text(encoding="utf-8")).get("metrics", {})
        prev_ndcg = previous.get("model", {}).get("similar_games", {}).get("ndcg@10")
        new_ndcg = metrics["model"]["similar_games"]["ndcg@10"]
        if prev_ndcg is not None and new_ndcg < prev_ndcg - NDCG_DROP_ALERT:
            ndcg_drop = True
            log(f"::warning::nDCG@10 bajó de {prev_ndcg:.3f} a {new_ndcg:.3f} respecto al modelo anterior")
    metrics["ndcg_drop"] = ndcg_drop
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"passed={str(metrics['passed']).lower()}\nndcg_drop={str(ndcg_drop).lower()}\n")

    args.metrics_dir.mkdir(parents=True, exist_ok=True)
    (args.metrics_dir / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown = metrics_markdown(metrics)
    (args.metrics_dir / "metrics.md").write_text(markdown, encoding="utf-8")
    log(markdown)

    if not metrics["passed"]:
        log("::error::El modelo no supera las metas; no se guarda.")
        return 1

    # Paso 6: guardar el modelo con su hash y el commit que lo generó.
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.model_out, compress=3)
    meta = {
        "sha256": sha256_file(args.model_out),
        "commit": metrics["commit"],
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "data_file": data_file.name,
        "n_games": len(df),
        "sklearn_version": sklearn.__version__,
        "clean_report": report,
        "metrics": {k: metrics[k] for k in ("model", "jaccard", "weights", "popularity_penalty")},
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"Modelo guardado en {args.model_out} (sha256 {meta['sha256'][:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
