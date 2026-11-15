# Rediseño de GameMatch: UI profesional "oscuro editorial"

## Contexto

GameMatch (repo `DevOps/`, FastAPI) sirve una sola página, `backend/app/static/index.html` (~440 líneas, HTML+CSS+JS sin build). Funciona, pero se ve como un prototipo: columna de 760 px, tarjetas que solo muestran título + barra de similitud, emojis como botones de voto, autocompletado con un `<datalist>` de 8.000 títulos y un login con dos botones submit.

El objetivo es una **demo/entrega del curso** que se vea como un producto real, de alto nivel, limpio y bonito, **sin romper CI, pruebas, Docker ni las reglas de privacidad** de la página.

Decisiones tomadas con el usuario:
- **Alcance:** visual + datos más ricos por juego (cambio pequeño en la API).
- **Estilo:** oscuro editorial (sobrio, tipo Linear/Raycast, un acento cálido, nada de neón).
- **Tecnología:** sin build; sigue siendo estático servido por FastAPI.

## Qué datos hay (verificado)

Cada juego del modelo (`models/gamematch.joblib` → `bundle["games"]`) tiene: `id`, `title`, `year` (int, 5 sin año), `genres`, `themes`, `game_modes`, `keywords` (listas), `rating_count`. **No hay resumen ni carátulas**: las tarjetas serán tipográficas, sin imágenes (no se reentrena ni se descarga nada de IGDB).

## Diseño

### 1. API: recomendaciones con detalle y "por qué"
- `app/model.py` · `ContentModel.recommend()`: además de `title` y `score`, devuelve `year`, `genres`, `themes`, `modes` y `reasons` (hasta 3 etiquetas compartidas entre el perfil del usuario —géneros/temas/modos de sus juegos + tags escritos— y el juego; sin "Indie", que lo tienen todos).
- `app/recommender.py` · `recommend_jaccard()`: mismo formato en el respaldo (`genres` = tags del catálogo manual, `year=None`, `reasons` = intersección con el perfil).
- `app/main.py` · `Recommendation`: campos nuevos opcionales con valor por defecto, así la respuesta sigue cumpliendo `{"title","score"} <= keys` (test_api.py:34).
- `/stats` sin cambios (ya da `catalog_size`, `recommender`, latencias p50/p95: se muestran en la página).
- Pruebas nuevas en `tests/test_model.py` y `tests/test_recommender.py`: los campos existen, `reasons` ⊆ etiquetas del juego, nunca incluye "Indie".

### 2. Archivos estáticos
- Separar en `static/index.html`, `static/app.css`, `static/app.js` y montar `StaticFiles` en `/static` (`app/main.py`). `/` sigue sirviendo `index.html`.
- Fuentes **auto-alojadas** en `static/fonts/` (woff2, licencia OFL) para no filtrar la IP de los usuarios a Google Fonts (coherente con el informe de privacidad). Propuesta: *Geist* (UI) + *Geist Mono* (números/ranking), desde el paquete npm de Fontsource vía jsDelivr. **Se pide permiso antes de descargarlas.** Fallback: pila de sistema.
- `tests/test_pagina.py`: leer `index.html` + `app.js` (no solo el HTML) para que las reglas sigan valiendo: sin `localStorage`/`sessionStorage`/`document.cookie`/`innerHTML`, token Bearer al votar, `resp.status === 401`, endpoints `/auth/login` y `/auth/register`.

### 3. La página (modo "Operate": precisión y calma, la marca vive en los detalles)
1. **Barra superior** fina y fija: wordmark "GameMatch", enlace "Cómo funciona", estado de sesión como píldora (alias + salir) o botón "Iniciar sesión".
2. **Hero + buscador** (primer pantallazo): titular grande ("Encuentra tu próximo indie."), una línea de apoyo, y el **compositor de búsqueda** como pieza central:
   - Control segmentado "Por juegos / Por géneros y temas".
   - Juegos: **input de tokens** (hasta 3 chips) con **combobox propio** accesible (`role="combobox"`, `aria-activedescendant`, flechas/Enter/Esc), filtrando en memoria los títulos ya cargados de `/games`; muestra 8 coincidencias. Reemplaza el `<datalist>`.
   - Tags: chips de los 14 tags más comunes + input libre (lógica actual de `toggleTag`/`currentTags`).
   - Selector de cantidad (5/10/20) y botón primario. Atajo `/` para enfocar.
3. **Resultados** en grilla (1 col móvil, 2 col desde 768 px):
   - Ranking en mono grande, título, año, píldoras de género (máx. 3), **afinidad %** relativa al mejor resultado con barra fina, línea "Comparte: Roguelike · Mitología" (de `reasons`).
   - Voto con iconos SVG propios (pulgar arriba/abajo) en vez de emojis; estado presionado con el acento.
   - Estados: **skeleton** mientras carga, vacío con sugerencias clicables (p. ej. "Prueba con Hades"), error con reintento. Entrada escalonada suave de tarjetas (respeta `prefers-reduced-motion`).
4. **Cómo funciona**: 3 pasos breves (tus juegos → vector de contenido → vecinos por Tanimoto) y cifras reales de `/stats` en mono (juegos en catálogo, motor activo, latencia p95).
5. **Pie**: "Datos de IGDB" · enlace a `/docs` (API) · motor activo.
6. **Diálogo de cuenta** rediseñado: pestañas "Entrar / Crear cuenta" con un solo botón primario, ayudas de alias/contraseña, errores en línea. Se conserva toda la lógica: token solo en memoria, voto pendiente que se envía tras login, Esc descarta el voto, re-login en 401.

### 4. Sistema visual
- Tokens CSS en `:root`: fondo casi negro (#0B0B0D aprox.), superficies escalonadas, bordes de 1 px de bajo contraste, texto primario/secundario/terciario, **un solo acento cálido** (ámbar/naranja) y rojo para errores. Contraste AA verificado.
- Escala tipográfica fija (12/14/16/20/28/48–64), tracking negativo en titulares, números tabulares.
- Radios 8/12/16, espaciado en múltiplos de 4, ancho máx. ~1120 px con bordes iguales.
- Micro-interacciones discretas: hover de tarjeta (sube 2 px + borde más claro), botones con estado press, foco visible con anillo del acento.
- Responsive verificado a 390, 768, 1024 y 1440 px.

## Skills a usar en la implementación
- **frontend-design** (Anthropic): se carga al inicio para fijar la dirección estética (tipografía, paleta, evitar decisiones genéricas) antes de escribir CSS; es la que define el "carácter" del oscuro editorial.
- **impeccable** (principal para el oficio): flujo de rediseño en modo *Operate*, `craft-floor.md` antes de editar, luego `polish` y `audit`.
- **awesome-design-md**: estilo de referencia `linear.app` (tokens, cromado de componentes), adaptado al acento cálido.
- **web-design-guidelines**: revisión final de accesibilidad/UX sobre `index.html`, `app.css`, `app.js`.
- *design-taste-frontend* no se usa como guía principal: está pensada para sitios de marketing con fotografías, y aquí no hay imágenes y la página es una herramienta.

## Archivos a modificar
- `backend/app/static/index.html` (reescrito), `backend/app/static/app.css` y `app.js` (nuevos), `backend/app/static/fonts/*.woff2` (nuevos)
- `backend/app/main.py` (montaje `/static`, modelo `Recommendation`)
- `backend/app/model.py` (`recommend`), `backend/app/recommender.py` (`recommend_jaccard`)
- `backend/tests/test_pagina.py`, `tests/test_model.py`, `tests/test_recommender.py`
- `docs/superpowers/specs/2026-11-11-rediseno-ui-design.md` (este diseño, convención del repo)
- Trabajo en rama nueva `feature/rediseno-ui` desde `main`; sin commit ni push hasta que el usuario lo pida.

## Verificación
1. `cd backend && ruff check . && pytest -v` → todo verde (incluidas las pruebas nuevas y las de privacidad de la página).
2. `uvicorn app.main:app --reload` y abrir `http://127.0.0.1:8000` en el navegador integrado:
   - búsqueda por juegos (Hollow Knight, Celeste, Hades) y por tags (roguelike, horror);
   - combobox con teclado, skeleton, estado vacío, error (juego inexistente → 400);
   - votar sin sesión → diálogo → registrar → el voto se envía; vencimiento → 401 → re-login;
   - capturas a 390, 768, 1024 y 1440 px, sin scroll horizontal; consola sin errores.
3. `docker build -t gamematch backend && docker run -p 8000:8000 gamematch` → la página carga fuentes y CSS desde `/static` dentro del contenedor (lo mismo que verifica el smoke test del CI).

## Decisiones finales de la implementación

- **La búsqueda es una frase:** «Me gustaron [juegos] y quiero algo parecido.» / «Busco un indie con [temas]». Las fichas y el campo van dentro del titular, con un combobox accesible (flechas, Enter, Esc, Backspace borra la última ficha; `/` enfoca el buscador).
- **Resultados como lista editorial** (no grilla de tarjetas): ranking, título, año, géneros, etiquetas «En común» y afinidad relativa al mejor resultado (el valor de Tanimoto queda en el `title`).
- **Paleta:** tinta `#16121b`, superficie `#201a26`, líneas `#3a3141`, papel `#f2eae0`, secundario `#b3a8b8`, ámbar `#f5a524` como único acento. Todos los textos ≥ 4,5:1.
- **Tipografía auto-alojada (OFL):** Anybody (ancho y peso variables) para la frase, títulos y cifras; Atkinson Hyperlegible Next para el texto.
- **Razones (`reasons`):** etiquetas compartidas con el perfil, ordenadas de la más rara a la más común del catálogo; se descartan «Indie», «Single player» y keywords de tienda/plataforma/eventos (Steam, logros, premios, PAX…) o con menos de 10 juegos.
- `/static` se sirve con `StaticFiles`; `.woff2` se registra como `font/woff2` para que el tipo sea correcto en cualquier sistema.

## Segunda vuelta: portadas, precios y escala de parecido

- **Datos de presentación aparte del modelo.** `python -m ml.media` descarga de IGDB la portada, la ficha y el id de Steam de cada juego, y de la tienda de Steam el precio base en Chile. Quedan en `backend/models/game_media.json`; el modelo y su SHA-256 no cambian. `app/media.py` lo carga validando la forma de cada valor, y si falta la API responde igual, sin portadas ni precios.
- **API.** `/recommendations` suma `cover_id`, `url`, `store_url` y `price`; `/games` suma `year` y `cover_id`; `/stats` suma `prices` (moneda y fecha). La lógica de recomendación no cambió.
- **Tarjetas.** Cuadrícula de 5 columnas con la portada como protagonista (5, 10 o 20 resultados llenan filas completas); bajo 1080 px, lista compacta con miniatura en 2 columnas y, bajo 680 px, en 1. Sin carrusel.
- **Parecido en escala fija**, sobre la similitud de Tanimoto real: por juegos, «Muy parecido» desde 0,40 y «Parecido» desde 0,25; por temas, desde 0,18 y 0,10 (un perfil de pocas palabras da similitudes más bajas). Los cortes salen de medir el modelo y no dependen de los demás resultados.
- **Español.** Géneros, temas y modos se muestran traducidos (`LABELS_ES` en `app.js`); el buscador por temas acepta español y envía la etiqueta de IGDB. Las keywords quedan en inglés, máximo 3.
- **Autocompletado** con portada y año; orden: título exacto, comienzo del nombre, comienzo de palabra, resto.
- **Hero.** Portadas del catálogo desplazándose lento detrás del titular (tenues y solo en el 60 % derecho; sin desenfoque ni máscara en vivo, que tenían la GPU ocupada el 94 % del tiempo, y en pausa cuando el buscador sale de la pantalla) y entrada del titular con GSAP por ESM desde jsDelivr (`hero.js`). Solo `transform` y `opacity`; con `prefers-reduced-motion` no hay entrada ni desplazamiento. Si GSAP no carga, la página se muestra igual.
- **Externo a la página:** las portadas (`images.igdb.com`, con `referrerpolicy="no-referrer"`) y GSAP (`cdn.jsdelivr.net`).
- **«Cómo elegimos tus juegos»** en lenguaje simple; TF-IDF, Tanimoto y p95 van en el desplegable «Para curiosos».
- **Precios:** son una foto del precio base (sin ofertas) con su fecha, indicada bajo los resultados.
