# GameMatch

# Actividad Práctica 1 — Arrancando con DevOps
*Asignatura: DevOps (Sec/ML)*

## Integrantes
- Nicolas Bravo — n.bravog@udd.cl
- Maximiliano Mella — m.mellac@udd.cl (PR de prueba Actividad 2)
- Martín Lorca — mlorca@udd.cl (PR de prueba Actividad 2 - listo)

## Objetivo del equipo

**Objetivo definido en la Actividad 2:** que las pruebas se ejecuten automáticamente antes de hacer merge.
✅ Logrado en la Actividad 3: cada Pull Request hacia `main` corre 11 pruebas en GitHub Actions y no se puede mergear si fallan.

**Objetivo actualizado (Entrega 1):** que todo cambio que llegue a `main` quede probado, empaquetado en una imagen Docker verificada y listo para desplegar, sin depender del computador ni de una sola persona del equipo.

## 1. Descripción del proyecto

GameMatch es una plataforma web que recomienda videojuegos indie a partir de los gustos del usuario, ingresados como juegos que ya conoce o géneros/mecánicas de su interés. Está dirigida a jugadores que buscan descubrir títulos poco conocidos fuera de los catálogos masivos, ofreciendo recomendaciones basadas en similitud de género, mecánicas y tono narrativo en lugar de en popularidad o publicidad.

## 2. Justificación de herramientas

**Lenguaje y framework:** Python + FastAPI, por su curva de aprendizaje suave, documentación automática de la API y facilidad para integrar el modelo de recomendación (Entrega 2) y para testear y containerizar en entregas futuras.

**Fuente de datos:** API de IGDB (Internet Game Database) para obtener el catálogo de juegos indie con sus géneros, tags y descripciones, evitando construir el catálogo manualmente.

**Control de versiones:** Git + GitHub, ya utilizado en clases, con soporte para colaboración, revisión de código (pull requests) e integración continua vía GitHub Actions.

**Gestión de tareas:** GitHub Projects, integrado con el repositorio: los issues se crean con plantilla y se mueven automáticamente al mergear el Pull Request vinculado. En la Actividad 1 se partió con Trello y en la Actividad 2 se migró a GitHub Projects para aprovechar esa automatización.

**Otras herramientas de colaboración:** Discord para comunicación diaria del equipo, y Notion para documentar decisiones de diseño (criterios de similitud, tags considerados, etc.).

Estas decisiones buscan aplicar desde el día uno los principios DevOps revisados en clase: ambiente de trabajo colaborativo, responsabilidad de extremo a extremo, automatización y mejora continua. Además, el stack elegido permite construir progresivamente hacia las siguientes entregas del curso: pipeline CI/CD básico (Entrega 1), incorporación de un modelo de recomendación por similitud (Entrega 2), contenedorización segura y prácticas DevSecOps (Entrega 3), y despliegue en Kubernetes (Entrega 4).

## 3. Repositorio de GitHub
Link: [https://github.com/niclasbravo/DevOps]

## 4. Tablero de trabajo (GitHub Projects)
Link: [https://github.com/users/niclasbravo/projects/1/views/1]

## 5. Backend, pruebas y contenedor

[![CI](https://github.com/niclasbravo/DevOps/actions/workflows/ci.yml/badge.svg)](https://github.com/niclasbravo/DevOps/actions/workflows/ci.yml)

API en FastAPI que recomienda juegos indie con un modelo de ML por contenido entrenado con datos de IGDB (ver sección 6). Si el modelo falta o no pasa la verificación de integridad, usa el recomendador base por similitud de tags (Jaccard) sobre un catálogo en memoria.

### Ejecutar con Docker (recomendado)

No requiere instalar Python: basta con tener Docker.

```bash
docker run -p 8000:8000 ghcr.io/niclasbravo/devops:latest
# API en http://localhost:8000/docs
```

Para construir la imagen desde el código:

```bash
docker build -t gamematch backend
docker run -p 8000:8000 gamematch
```

### Desarrollo local sin Docker

```bash
cd backend
pip install -r requirements-dev.txt
uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
pytest -v                       # corre las pruebas
```

### Pipeline (`.github/workflows/ci.yml`)

| Job | Cuándo corre | Qué hace |
|---|---|---|
| Build y pruebas (pytest) | Cada PR hacia `main` y cada push a `main` | Instala dependencias, revisa estilo con ruff, verifica el SHA-256 del modelo y corre las pruebas |
| Docker (build, smoke test y publicación) | Después de que pasen las pruebas | Construye la imagen, la levanta, verifica `/health`, `/recommendations` y que `/stats` informe el modelo ML activo, comprueba que no corre como root y, solo al mergear a `main`, la publica en GitHub Packages (`latest` y `sha-<commit>`) |

## 6. Modelo de recomendación (ML)

Implementa el diseño de `Informe_Solucion_ML.pdf`: recomendación **basada en contenido** (no supervisada). Cada juego
se convierte en un vector que une el **TF-IDF** de su resumen e historia con sus géneros, temas, modos de juego y
keywords codificados como 0/1. Para recomendar se buscan los vecinos más cercanos (**k-NN con similitud de Tanimoto**) al
perfil del usuario, que es el promedio de sus juegos favoritos más sus tags de interés. La función `recommend()`
mantiene su firma, así que la API no cambió.

### Flujo

| Paso | Dónde | Qué hace |
|---|---|---|
| 1 | `ml/fetch.py` | Descarga juegos indie de IGDB (género 32, con resumen, sin DLC ni bundles) a `data/raw/igdb_<fecha>.csv` |
| 2 | `ml/clean.py` | Une duplicados (nombre normalizado + año), deja solo texto en inglés, quita keywords raras y **detiene el pipeline** si faltan columnas o hay demasiados vacíos |
| 3-4 | `ml/features.py` | TF-IDF (scikit-learn) + categorías 0/1, cada bloque con su peso; índice de vecinos |
| 5 | `ml/evaluate.py` | Precision@10, nDCG@10, cobertura y latencia p95 contra el Jaccard actual **sobre el mismo catálogo**, usando `similar_games` de IGDB y las consultas del equipo (`ml/eval_queries.json`) |
| 6 | `ml/train.py` | Si supera las metas, guarda `models/gamematch.joblib` y `models/model_meta.json` (SHA-256, commit, métricas). Si no, termina con error y no guarda nada |
| 7 | `ci.yml` | El CI de siempre verifica el hash, prueba la API y construye la imagen Docker con el modelo |
| 8-10 | `app/` | `/recommendations` usa el modelo; `POST /feedback` guarda votos anónimos; `/stats` muestra la latencia p50/p95 y la versión del modelo. `entrenamiento.yml` reentrena cada mes y avisa a Discord si baja el nDCG |

### Métricas del modelo actual

Entrenado con 7.996 juegos indie de IGDB (descarga del 26-10-2026). Las métricas se miden sobre consultas que
no se usaron para ajustar los pesos:

| Referencia | Métrica | Modelo | Jaccard | Meta |
|---|---|---|---|---|
| `similar_games` de IGDB (3.937 consultas) | Precision@10 | **0,207** | 0,199 | Mayor que Jaccard ✅ |
| `similar_games` de IGDB (3.937 consultas) | nDCG@10 | **0,330** | 0,318 | Mayor que Jaccard ✅ |
| Consultas del equipo (30) | Precision@10 | **0,060** | 0,053 | Mayor que Jaccard ✅ |
| Consultas del equipo (30) | nDCG@10 | **0,131** | 0,116 | Mayor que Jaccard ✅ |
| Catálogo | Cobertura | **91,2 %** | 88,8 % | ≥ 40 % ✅ |
| API | Latencia p95 | **2,8 ms** | – | < 200 ms ✅ |

**Hallazgo de la evaluación: Tanimoto en vez de coseno.** El informe proponía similitud coseno, pero con coseno el
modelo **no superaba** a Jaccard en `similar_games` (nDCG@10 de 0,225 contra 0,318), aunque sí en las consultas del
equipo. Incluso usando solo las etiquetas, el coseno quedaba bajo Jaccard, y darle más peso al texto siempre
empeoraba el resultado. Todo indica que IGDB calcula `similar_games` a partir de etiquetas compartidas, lo que
favorece a Jaccard. Por eso se usa la **similitud de Tanimoto**, x·p / (‖x‖² + ‖p‖² − x·p), que es la
generalización de Jaccard a vectores con pesos: con vectores 0/1 da exactamente Jaccard (hay una prueba que lo
verifica), y el TF-IDF del resumen aporta lo que las etiquetas no capturan. El vector y el k-NN son los del informe.

Limitaciones conocidas: las 30 consultas del equipo (`ml/eval_queries.json`) son un primer borrador que el equipo
debe revisar; 7 títulos esperados no están en el catálogo y se ignoran. Las búsquedas solo por tags tienden a
devolver juegos poco conocidos con pocas etiquetas, y conviene revisarlas con los votos de `/feedback`.

### Entrenar en local

```bash
cd backend
pip install -r requirements-dev.txt
# backend/.env (no se versiona): TWITCH_CLIENT_ID=...  TWITCH_CLIENT_SECRET=...
python -m ml.fetch            # paso 1: descarga a data/raw/
python -m ml.train            # pasos 2 a 6: imprime la tabla modelo vs Jaccard
```

Si IGDB limita las consultas, el respaldo es el dataset de Steam de Kaggle:
`python -m ml.fetch --source kaggle --csv games.csv`.

### Reentrenamiento automático (`.github/workflows/entrenamiento.yml`)

Corre el día 1 de cada mes (o a mano desde Actions). Descarga, entrena y evalúa. Si el modelo supera a Jaccard,
abre un PR `ml/reentrenamiento-<fecha>` con el modelo nuevo y la tabla de métricas, y ese PR pasa por el CI y la
revisión como cualquier otro cambio. Los datos y las métricas quedan como artefacto del run.

Configuración necesaria en *Settings → Secrets and variables → Actions*:

| Nombre | Tipo | Para qué |
|---|---|---|
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | Secret | Acceso a la API de IGDB |
| `ML_PR_TOKEN` | Secret (opcional) | PAT para abrir el PR; con `GITHUB_TOKEN` el PR no dispara el CI y hay que correrlo a mano |
| `DISCORD_WEBHOOK` | Secret (ya existe) | Alerta si el entrenamiento falla o baja el nDCG |

Además hay que activar *Settings → Actions → General → Allow GitHub Actions to create and approve pull requests*.

### Seguridad

- **Token de IGDB:** solo en secrets de Actions y en `backend/.env`, que está en `.gitignore`.
- **Modelo alterado:** joblib usa pickle, que puede ejecutar código al cargarse. La API calcula el SHA-256 del
  archivo y lo compara con `model_meta.json` **antes** de cargarlo; si no coincide, lo rechaza y usa Jaccard.
- **Privacidad de los votos:** `/feedback` acepta solo un id anónimo, el juego y el voto.
- **Sesgo de popularidad:** se mide la cobertura del catálogo y, si queda bajo 40 %, el entrenamiento aplica una
  penalización a los juegos más valorados.

## 7. Accesos y cierre de Shadow IT

Revisión hecha al cierre de la Entrega 1 para confirmar que ninguna credencial ni decisión del proyecto depende de una sola persona.

| Recurso | Quién tiene acceso | ¿Depende de una sola persona? |
|---|---|---|
| Repositorio GitHub | Nicolas (owner/admin); Maximiliano y Martín (write) | No en la práctica. GitHub no permite dar Admin a colaboradores en repositorios personales (ver Actividad 2). Se mitiga con la protección de `main`, que **también aplica al admin**: ningún cambio entra sin PR, CI en verde y la aprobación de otro integrante. |
| Tablero del proyecto | Los 3 integrantes como admin | No. Pendiente de la auditoría de la Actividad 2, cerrado. |
| Canal de Discord y webhook de GitHub | Nicolas (dueño del servidor); Maximiliano y Martín con rol Administrador | No. Cualquiera de los 3 puede gestionar el canal y el webhook |
| Credenciales del pipeline | El CI usa el `GITHUB_TOKEN` que GitHub genera en cada ejecución | No |
| App de Twitch para IGDB (`TWITCH_CLIENT_ID/SECRET`) | Registrada en la cuenta de Twitch de Maximiliano; el secret está en los secrets del repo | **Sí, pendiente:** mover la app a una organización de Twitch o documentar cómo regenerarla desde otra cuenta |
| Imagen Docker publicada | Vinculada al repositorio en GitHub Packages | No. Se publica sola desde el pipeline, no desde el computador de nadie |
| Secretos en el código | No hay claves, tokens ni contraseñas en el repositorio (revisado) | No |

**Decisiones técnicas:** se toman en Pull Requests revisados por otro integrante y quedan registradas en el historial del repositorio. El rol de tech coach rota entre los integrantes en cada mini-Dojo.
