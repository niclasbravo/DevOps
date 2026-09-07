# DevOps

# Actividad Práctica 1 — Arrancando con DevOps
*Asignatura: DevOps (Sec/ML)*

## Integrantes
- [Nicolas Bravo — [n.bravog@udd.cl]
- [Maximiliano Mella] — [n.bravog@udd.cl]
- [Martin Lorca] — [mlorca@udd.cl]

## 1. Descripción del proyecto

GameMatch es una plataforma web que recomienda videojuegos indie a partir de los gustos del usuario, ingresados como juegos que ya conoce o géneros/mecánicas de su interés. Está dirigida a jugadores que buscan descubrir títulos poco conocidos fuera de los catálogos masivos, ofreciendo recomendaciones basadas en similitud de género, mecánicas y tono narrativo en lugar de en popularidad o publicidad.

## 2. Justificación de herramientas

**Lenguaje y framework:** Python + FastAPI, por su curva de aprendizaje suave, documentación automática de la API y facilidad para integrar el modelo de recomendación (Entrega 2) y para testear y containerizar en entregas futuras.

**Fuente de datos:** API de IGDB (Internet Game Database) para obtener el catálogo de juegos indie con sus géneros, tags y descripciones, evitando construir el catálogo manualmente.

**Control de versiones:** Git + GitHub, ya utilizado en clases, con soporte para colaboración, revisión de código (pull requests) e integración continua vía GitHub Actions.

**Gestión de tareas:** Trello, por su simplicidad para visualizar el flujo de trabajo del equipo en columnas (Por hacer / En progreso / Hecho).

**Otras herramientas de colaboración:** Discord para comunicación diaria del equipo, y Notion para documentar decisiones de diseño (criterios de similitud, tags considerados, etc.).

Estas decisiones buscan aplicar desde el día uno los principios DevOps revisados en clase: ambiente de trabajo colaborativo, responsabilidad de extremo a extremo, automatización y mejora continua. Además, el stack elegido permite construir progresivamente hacia las siguientes entregas del curso: pipeline CI/CD básico (Entrega 1), incorporación de un modelo de recomendación por similitud (Entrega 2), contenedorización segura y prácticas DevSecOps (Entrega 3), y despliegue en Kubernetes (Entrega 4).

## 3. Repositorio de GitHub
Link: [https://github.com/niclasbravo/DevOps]

## 4. Tablero de trabajo (Trello)
Link: [https://trello.com/b/8W1BZoVG]
