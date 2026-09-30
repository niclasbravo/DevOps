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

API mínima en FastAPI con un recomendador por similitud de tags (Jaccard) sobre un catálogo en memoria.

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
| Build y pruebas (pytest) | Cada PR hacia `main` y cada push a `main` | Instala dependencias y corre las 11 pruebas |
| Docker (build, smoke test y publicación) | Después de que pasen las pruebas | Construye la imagen, la levanta, verifica `/health` y `/recommendations`, comprueba que no corre como root y, solo al mergear a `main`, la publica en GitHub Packages (`latest` y `sha-<commit>`) |

## 6. Accesos y cierre de Shadow IT

Revisión hecha al cierre de la Entrega 1 para confirmar que ninguna credencial ni decisión del proyecto depende de una sola persona.

| Recurso | Quién tiene acceso | ¿Depende de una sola persona? |
|---|---|---|
| Repositorio GitHub | Nicolas (owner/admin); Maximiliano y Martín (write) | No en la práctica. GitHub no permite dar Admin a colaboradores en repositorios personales (ver Actividad 2). Se mitiga con la protección de `main`, que **también aplica al admin**: ningún cambio entra sin PR, CI en verde y la aprobación de otro integrante. |
| Tablero del proyecto | Los 3 integrantes como admin | No. Pendiente de la auditoría de la Actividad 2, cerrado. |
| Canal de Discord y webhook de GitHub | Nicolas (dueño del servidor); Maximiliano y Martín con rol Administrador | No. Cualquiera de los 3 puede gestionar el canal y el webhook |
| Credenciales del pipeline | Ninguna personal. El CI usa el `GITHUB_TOKEN` que GitHub genera en cada ejecución | No |
| Imagen Docker publicada | Vinculada al repositorio en GitHub Packages | No. Se publica sola desde el pipeline, no desde el computador de nadie |
| Secretos en el código | No hay claves, tokens ni contraseñas en el repositorio (revisado) | No |

**Decisiones técnicas:** se toman en Pull Requests revisados por otro integrante y quedan registradas en el historial del repositorio. El rol de tech coach rota entre los integrantes en cada mini-Dojo.
