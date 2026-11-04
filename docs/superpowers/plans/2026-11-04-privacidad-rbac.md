# Privacidad y RBAC en GameMatch: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar en `backend/app/` la minimización de datos, la seudonimización, la anonimización y el RBAC definidos en la spec, con pruebas que demuestren accesos permitidos y denegados.

**Architecture:** Módulos pequeños sobre la API FastAPI existente: `crypto.py` y `db.py` son la base, `auth.py` y `audit.py` controlan el acceso, y `reports.py`, `logging_safe.py` y `errors.py` limitan lo que sale del sistema. La autorización es una dependencia por endpoint, y los permisos se leen de la base en cada petición.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, `sqlite3` (biblioteca estándar), `argon2-cffi`, `pyjwt`, `cryptography`, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-04-privacidad-rbac-design.md`

## Global Constraints

- Nada entra a `main` sin PR, CI en verde y la aprobación de otro integrante. Nunca push directo a `main`.
- Variables de entorno: `GM_DB_PATH`, `GM_PSEUDONYM_KEY`, `GM_EMAIL_KEY`, `GM_JWT_SECRET`. Claves de al menos 32 bytes, una por propósito, sin valores por defecto.
- Sin claves la API arranca; lo público funciona y lo que necesita claves responde 503.
- Nunca guardar ni registrar IP, nombre, fecha de nacimiento, ni consultas a `/recommendations`.
- Fechas de `feedback.voted_on` y `accounts.created_on`: solo `YYYY-MM-DD`.
- Modelos Pydantic de entrada con `extra="forbid"`. SQL siempre con parámetros.
- Argon2id; contraseña de 12 a 128 caracteres; JWT HS256 de 30 minutos; bloqueo de 15 minutos tras 5 fallos; umbral de reporte de 5 votos.
- `/stats` conserva `recommender`, `catalog_size` y `latency_ms`, para que el smoke test del CI no cambie.
- Ruff: `line-length = 120`, reglas `E`, `F`, `I`. Código y mensajes en español, como el resto del repo.
- El token de la página vive solo en una variable de JavaScript: nunca en `localStorage`, `sessionStorage` ni cookies.
- Pruebas solo con datos sintéticos y claves generadas en cada corrida.
- Usar Python 3.12 (`py -3.12`), porque es la versión del CI y de la imagen.

## Review Focus

- Volver a votar el mismo juego debe actualizar el voto, no fallar con 500 por el `UNIQUE`. Prueba en Task 3.
- `Nico` y `nico` deben ser la misma cuenta, o se podría suplantar a alguien con un alias casi igual. Prueba en Task 4.
- Una contraseña de 1 MB debe rechazarse con 422 antes de llegar a Argon2, porque si no sirve para saturar el servidor. Prueba en Task 4.
- Un token válido de una cuenta borrada o suspendida debe dar 401, no 500. Prueba en Task 5.
- Un token vencido mientras la página sigue abierta debe volver a pedir el login, no mostrar un error sin salida. Comprobación en Task 7.
- Una línea corrupta en `feedback.jsonl` no debe abortar la migración ni filtrarse al log, y migrar dos veces no debe duplicar votos. Prueba en Task 3.

## Flujo por PR

Cada grupo de tareas es un PR. Antes de empezar: issue en el tablero y rama desde `main` actualizado.

El CI solo corre en PRs hacia `main`. Si el PR anterior aún espera revisión, la rama siguiente puede partir de él con un PR apuntando a esa rama, pero el CI no correrá hasta que el PR apunte a `main`. Mientras tanto, se corren localmente:

```bash
cd backend && py -3.12 -m pytest -v && py -3.12 -m ruff check .
```

Ningún PR se mergea sin que un humano del equipo lo apruebe.

---

## PR 2: `feature/bd-minimizacion`

### Task 1: Criptografía y claves

**Files:**
- Create: `backend/app/crypto.py`, `backend/tests/test_crypto.py`
- Modify: `backend/requirements.txt`, `backend/tests/conftest.py`

**Interfaces:**
- Produces: `class MissingKeyError(RuntimeError)`, `get_key(name: str) -> bytes`, `pseudonymize(value: str) -> str` (hex de 64 caracteres), `hash_password(password: str) -> str`, `verify_password(stored: str, password: str) -> bool`, `needs_rehash(stored: str) -> bool`, `encrypt_email(email: str) -> bytes`, `decrypt_email(token: bytes) -> str`.
- Produces: fixture `autouse` `entorno_seguro` en `conftest.py`, que genera las tres claves con `secrets.token_urlsafe(48)` y apunta `GM_DB_PATH` a `tmp_path`.

- [ ] **Step 1: Escribir las pruebas**

```python
def test_seudonimo_es_estable_y_no_contiene_el_id():
    assert pseudonymize("abc") == pseudonymize("abc")
    assert "abc" not in pseudonymize("abc") and len(pseudonymize("abc")) == 64

def test_seudonimo_cambia_con_la_clave(monkeypatch):
    antes = pseudonymize("abc")
    monkeypatch.setenv("GM_PSEUDONYM_KEY", "x" * 40)
    assert pseudonymize("abc") != antes

def test_hash_es_argon2id_y_verifica():
    h = hash_password("una-clave-larga-1")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "una-clave-larga-1") and not verify_password(h, "otra-clave-larga-2")

def test_correo_cifrado_no_es_legible():
    token = encrypt_email("persona@example.com")
    assert b"persona" not in token and decrypt_email(token) == "persona@example.com"

@pytest.mark.parametrize("valor", [None, "corta"])
def test_clave_ausente_o_debil_falla(monkeypatch, valor):
    monkeypatch.delenv("GM_PSEUDONYM_KEY", raising=False)
    if valor:
        monkeypatch.setenv("GM_PSEUDONYM_KEY", valor)
    with pytest.raises(MissingKeyError):
        pseudonymize("abc")
```

- [ ] **Step 2: Verificar que fallan** — `py -3.12 -m pytest tests/test_crypto.py -v`. Esperado: `ModuleNotFoundError: app.crypto`.
- [ ] **Step 3: Implementar `crypto.py`** — `argon2.PasswordHasher` con parámetros por defecto; HMAC con `hmac` y `hashlib.sha256`. `GM_EMAIL_KEY` admite varias claves separadas por coma: cada una se deriva a 32 bytes con SHA-256 y base64 urlsafe, y se usan con `MultiFernet`. Agregar a `requirements.txt`: `argon2-cffi>=23.1`, `pyjwt>=2.8`, `cryptography>=42`.
- [ ] **Step 4: Verificar** — el mismo comando. Esperado: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Agrega seudónimos, hash de contraseñas y cifrado de correo"`

### Task 2: Base de datos

**Files:**
- Create: `backend/app/db.py`, `backend/tests/test_db.py`

**Interfaces:**
- Produces: `connect() -> sqlite3.Connection` (`row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`, crea el directorio padre), `init_db() -> None` (idempotente; crea el esquema de la spec 2.2 y carga roles y permisos), `ROLE_PERMISSIONS: dict[str, set[str]]`.

`ROLE_PERMISSIONS` sigue la tabla 4.2 de la spec. Una conexión por petición, porque `sqlite3` no se comparte entre hilos. `accounts.alias` usa `COLLATE NOCASE`.

- [ ] **Step 1: Escribir las pruebas**

```python
def test_init_db_es_idempotente(): ...  # dos llamadas; 3 roles
@pytest.mark.parametrize("sql", ["UPDATE audit_log SET action='x'", "DELETE FROM audit_log"])
def test_audit_log_solo_admite_inserciones(sql):
    # inserta una fila y espera sqlite3.IntegrityError con "solo inserción"
def test_voto_invalido_lo_rechaza_la_base(): ...  # vote=5 lanza IntegrityError
def test_feedback_no_tiene_clave_foranea():
    assert connect().execute("PRAGMA foreign_key_list(feedback)").fetchall() == []
```

- [ ] **Step 2: Verificar que fallan** — `py -3.12 -m pytest tests/test_db.py -v`. Esperado: FAIL por módulo inexistente.
- [ ] **Step 3: Implementar `db.py`** con el SQL de la spec, sentencias `IF NOT EXISTS` e `INSERT OR IGNORE`.
- [ ] **Step 4: Verificar.** Esperado: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Agrega esquema SQLite con auditoría de solo inserción"`

### Task 3: Votos en SQLite, validaciones y migración

**Files:**
- Create: `backend/app/cli.py`, `backend/tests/test_privacidad.py`
- Modify: `backend/app/main.py` (`Feedback`, `post_feedback`, `lifespan`, modelos de entrada), `backend/tests/test_api.py`, `backend/tests/test_db.py`

**Interfaces:**
- Consumes: `connect`, `init_db`, `pseudonymize`, `MissingKeyError`.
- Produces: `save_vote(pseudonym: str, game: str, vote: int) -> None` en `db.py`; `migrate_feedback(path: Path) -> tuple[int, int]` en `cli.py`, que devuelve `(migrados, omitidos)`.

`save_vote` hace `INSERT ... ON CONFLICT(pseudonym, game) DO UPDATE`. Un `MissingKeyError` se traduce a 503 con un manejador global en `main.py`. El `lifespan` llama a `init_db()`.

- [ ] **Step 1: Escribir las pruebas**

```python
def test_voto_guarda_seudonimo_y_solo_fecha():
    # POST /feedback; la fila no contiene "anon-123" y voted_on calza con ^\d{4}-\d{2}-\d{2}$
def test_campo_extra_se_rechaza():
    body = {"user_id": "a", "game": "Hades", "vote": 1, "birthdate": "2000-01-01"}
    assert client.post("/feedback", json=body).status_code == 422
def test_volver_a_votar_actualiza_el_voto(): ...  # dos POST, 201 ambos, una fila, vote=-1
def test_sin_clave_feedback_responde_503_y_lo_publico_funciona(monkeypatch):
    monkeypatch.delenv("GM_PSEUDONYM_KEY")
    # POST /feedback da 503; GET /health y POST /recommendations dan 200
def test_migracion_omite_linea_corrupta_y_borra_archivo(tmp_path, caplog):
    # 2 líneas válidas y 1 corrupta -> (2, 1); archivo borrado; el contenido corrupto no está en caplog.text
def test_migrar_dos_veces_no_duplica(tmp_path): ...
```

- [ ] **Step 2: Verificar que fallan.**
- [ ] **Step 3: Implementar.** `migrate_feedback` usa `pseudonymize(f"legacy:{user_id}")` y los primeros 10 caracteres de `ts`. Borra el archivo solo si la transacción terminó bien.
- [ ] **Step 4: Verificar** — `py -3.12 -m pytest -v`. Esperado: toda la suite en PASS.
- [ ] **Step 5: Commit y PR 2.** Comprobar ruff, push y abrir el PR con la plantilla.

---

## PR 3: `feature/auth-rbac`

### Task 4: Registro y login

**Files:**
- Create: `backend/app/auth.py`, `backend/tests/test_auth.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Consumes: `connect`, `hash_password`, `verify_password`, `needs_rehash`, `get_key`, `encrypt_email`.
- Produces: `router` de `auth.py`; `create_token(account_id: str) -> str`; `decode_token(token: str) -> str`, que devuelve el `sub` o lanza `jwt.InvalidTokenError`.

`POST /auth/register` recibe `alias`, `password` y `email` opcional, y responde 201 con `{"alias": ...}`. `POST /auth/login` responde `{"access_token": ..., "token_type": "bearer"}`. Patrón de `alias`: `^[A-Za-z0-9_-]{3,30}$`. Con alias inexistente se verifica contra un hash señuelo, y el mensaje siempre es `Credenciales inválidas` (401).

- [ ] **Step 1: Escribir las pruebas**

```python
def test_registro_crea_usuario_y_no_acepta_rol():
    # con "role": "admin" en el cuerpo da 422; sin él da 201 y el rol es 'usuario'
def test_alias_no_distingue_mayusculas(): ...  # "Nico" y luego "nico" da 409
def test_alias_con_formato_de_correo_se_rechaza(): ...  # 422
@pytest.mark.parametrize("largo", [11, 129, 1_000_000])
def test_largo_de_contrasena_invalido_da_422(largo): ...
def test_login_mismo_error_si_el_alias_no_existe():
    # mismo status y mismo cuerpo que con contraseña incorrecta
def test_bloqueo_tras_cinco_fallos(): ...  # el sexto intento falla aunque la clave sea correcta
def test_token_vencido_o_manipulado_es_invalido(): ...
```

- [ ] **Step 2: Verificar que fallan.**
- [ ] **Step 3: Implementar `auth.py`** y registrar el router.
- [ ] **Step 4: Verificar.** Esperado: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Agrega registro y login con bloqueo de intentos"`

### Task 5: Autorización, auditoría y datos propios

**Files:**
- Create: `backend/app/audit.py`
- Modify: `backend/app/auth.py`, `backend/app/main.py`, `backend/tests/test_api.py`, `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `decode_token`, `pseudonymize`, `save_vote`.
- Produces: `record(action: str, outcome: str, actor_id: str | None = None, target_id: str | None = None) -> None`; `current_account(...) -> sqlite3.Row`; `require_permission(code: str)`, una dependencia que devuelve la cuenta; `is_last_admin(account_id: str) -> bool`.

Usar `HTTPBearer(auto_error=False)` para responder 401 y no 403 cuando falta el token. `record` seudonimiza los ids. `require_permission` audita cada 401 y 403. Rutas: `GET /me` (`alias`, `role`, `created_on`, `has_email`), `GET /me/feedback`, `PATCH /me` (exige `current_password`), `DELETE /me` (409 si `is_last_admin`). `POST /feedback` usa `require_permission("feedback:write")` y deja de aceptar `user_id`. `/stats` pierde el bloque `model`.

- [ ] **Step 1: Escribir las pruebas**

```python
def test_token_de_cuenta_borrada_o_suspendida_da_401(): ...
def test_cambio_de_rol_rige_de_inmediato(): ...  # mismo token, tras el cambio da 403
def test_feedback_sin_token_da_401_y_rechaza_user_id(): ...
def test_usuario_solo_ve_sus_votos(): ...
def test_patch_me_exige_contrasena_actual(): ...
def test_borrar_cuenta_conserva_votos_sin_vinculo(): ...
def test_denegacion_queda_auditada(): ...  # fila con outcome 'denegado'
def test_stats_no_expone_datos_del_modelo():
    assert "model" not in client.get("/stats").json()
```

- [ ] **Step 2: Verificar que fallan.**
- [ ] **Step 3: Implementar.** Ajustar `test_api.py` al nuevo contrato de `/feedback`.
- [ ] **Step 4: Verificar.** Esperado: toda la suite en PASS.
- [ ] **Step 5: Commit** — `git commit -m "Aplica permisos por endpoint y auditoría"`

### Task 6: Administración y matriz completa

**Files:**
- Create: `backend/tests/test_rbac.py`
- Modify: `backend/app/main.py`, `backend/app/cli.py`, `backend/app/auth.py`

**Interfaces:**
- Consumes: `require_permission`, `record`, `is_last_admin`, `decrypt_email`.
- Produces: fixture `cuentas` en `conftest.py`, que entrega un token por rol; comando `python -m app.cli create-admin --alias <alias>`, que pide la contraseña con `getpass`.

Rutas de la tabla 4.2: `GET /admin/model`, `GET /admin/accounts` (`id`, `alias`, `role`, `is_active`), `PATCH /admin/accounts/{id}/role`, `PATCH /admin/accounts/{id}/status`, `POST /admin/accounts/{id}/reveal-email`, `GET /admin/audit`. Cambiar el propio rol da 403; dejar el sistema sin admin da 409.

- [ ] **Step 1: Escribir las pruebas**

```python
MATRIZ = [
    # (rol, método, ruta, código esperado)
    (None, "GET", "/me", 401), ("usuario", "GET", "/me", 200),
    ("usuario", "GET", "/reports/feedback", 403), ("analista", "GET", "/admin/accounts", 403),
    ("analista", "GET", "/admin/audit", 403), ("usuario", "GET", "/admin/model", 403),
    ("admin", "GET", "/admin/accounts", 200), ("admin", "GET", "/admin/audit", 200),
    ("analista", "POST", "/feedback", 403),
    # una fila por cada celda de las tablas 4.2 y 4.3 de la spec
]

@pytest.mark.parametrize("rol,metodo,ruta,esperado", MATRIZ)
def test_matriz_rbac(cuentas, rol, metodo, ruta, esperado): ...

def test_lista_de_cuentas_no_expone_correo_ni_hash(): ...
def test_admin_no_cambia_su_propio_rol(): ...
def test_no_se_puede_dejar_sin_admin(): ...
def test_ver_correo_queda_auditado(): ...
def test_ninguna_respuesta_contiene_claves(): ...
```

`GET /reports/feedback` se agrega en Task 8. Hasta entonces, marcar esas filas con `pytest.param(..., marks=pytest.mark.xfail(strict=True))` y quitar la marca en Task 8.

- [ ] **Step 2: Verificar que fallan.**
- [ ] **Step 3: Implementar** las rutas y `create-admin`.
- [ ] **Step 4: Verificar.** Esperado: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Agrega administración de cuentas y matriz RBAC"`

### Task 7: Login en la página web

**Files:**
- Create: `backend/tests/test_pagina.py`
- Modify: `backend/app/static/index.html`

**Interfaces:**
- Consumes: `POST /auth/register`, `POST /auth/login`, `POST /feedback` (Tasks 4 y 5).
- Produces: variable de módulo `let token = null` en el script de la página; `login(alias, password)`, `register(alias, password)`, `logout()`.

Recomendar sigue público. Al pulsar 👍 o 👎 sin sesión se muestra el formulario, sin perder los resultados. Con 401 en `/feedback`, `logout()` y se vuelve a pedir el login. El registro no pide correo en la página. Se mantiene el estilo actual y el uso de `textContent`.

- [ ] **Step 1: Escribir las pruebas**

```python
HTML = (Path(app.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

@pytest.mark.parametrize("prohibido", ["localStorage", "sessionStorage", "document.cookie", "gamematch-uid", "user_id", "innerHTML"])
def test_pagina_no_guarda_identificadores_ni_usa_html_dinamico(prohibido):
    assert prohibido not in HTML

def test_pagina_envia_el_token_al_votar():
    assert "Authorization" in HTML and "Bearer" in HTML
```

- [ ] **Step 2: Verificar que fallan** — `py -3.12 -m pytest tests/test_pagina.py -v`. Esperado: FAIL con `localStorage`.
- [ ] **Step 3: Implementar** los cambios en `index.html`.
- [ ] **Step 4: Verificar en el navegador.** Levantar la API con claves y comprobar: recomendar sin sesión funciona; votar sin sesión abre el formulario; registrarse y votar guarda el voto; recargar cierra la sesión; `localStorage` queda vacío.
- [ ] **Step 5: Commit y PR 3.**

---

## PR 4: `feature/anonimizacion-logs`

### Task 8: Reporte anónimo

**Files:**
- Create: `backend/app/reports.py`, `backend/tests/test_reportes.py`
- Modify: `backend/app/main.py`, `backend/tests/test_rbac.py`

**Interfaces:**
- Produces: `feedback_report(min_votes: int = 5) -> list[dict]`, con claves `game`, `votos` y `positivos`. Ruta `GET /reports/feedback` con `require_permission("reports:read")`.

- [ ] **Step 1: Escribir las pruebas**

```python
def test_juego_con_cuatro_votos_se_omite_y_con_cinco_aparece(): ...
def test_reporte_no_tiene_seudonimos_ni_fechas():
    assert set(fila) == {"game", "votos", "positivos"}
```

- [ ] **Step 2: Verificar que fallan.**
- [ ] **Step 3: Implementar** con `GROUP BY game HAVING COUNT(*) >= ?`, y quitar los `xfail` de `test_rbac.py`.
- [ ] **Step 4: Verificar.** Esperado: PASS.
- [ ] **Step 5: Commit** — `git commit -m "Agrega reporte anónimo con umbral"`

### Task 9: Errores y logs seguros

**Files:**
- Create: `backend/app/errors.py`, `backend/app/logging_safe.py`, `backend/tests/test_logs_errores.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `register_error_handlers(app: FastAPI) -> None`; `RedactingFilter(logging.Filter)`; `configure_logging() -> None`.

El manejador de `RequestValidationError` devuelve `{"detail": [{"campo": ..., "error": ...}]}`, sin `input`, `ctx` ni `url`. El de `Exception` responde 500 con `{"detail": "Error interno", "error_id": <uuid4>}`. El filtro reemplaza correos, `Bearer ...` y JWT por `[REDACTADO]`.

- [ ] **Step 1: Escribir las pruebas**

```python
def test_422_no_devuelve_la_contrasena():
    r = client.post("/auth/register", json={"alias": "x", "password": "mi-clave-secreta-1"})
    assert r.status_code == 422 and "mi-clave-secreta-1" not in r.text

def test_500_es_generico_y_no_filtra_la_excepcion(): ...
def test_filtro_redacta_correo_y_token(): ...
def test_login_no_deja_alias_ni_clave_en_logs(caplog): ...
def test_sql_injection_en_login_no_autentica(): ...  # alias "' OR 1=1 --" da 422
```

Para el 500, usar `TestClient(app, raise_server_exceptions=False)` y una ruta de prueba que lance una excepción con un texto secreto.

- [ ] **Step 2: Verificar que fallan.**
- [ ] **Step 3: Implementar** y registrar en `main.py`.
- [ ] **Step 4: Verificar.** Esperado: toda la suite en PASS.
- [ ] **Step 5: Commit y PR 4.**

---

## PR 5: `docs/informe-actividad`

### Task 10: Despliegue, evidencias e informe

**Files:**
- Modify: `backend/Dockerfile` (`GM_DB_PATH` reemplaza a `FEEDBACK_PATH`), `README.md` (variables, volumen, roles y corrección de "Privacidad de los votos")
- Create: `backend/.env.example` (solo nombres de variables), `docs/informe-privacidad-rbac.md`

- [ ] **Step 1: Comprobar la imagen.** Construirla y levantarla con claves y un volumen, y confirmar: sin claves, `/health` da 200 y `/feedback` da 503; con claves, un voto sobrevive a recrear el contenedor; `/stats` no trae `model`.
- [ ] **Step 2: Reunir evidencias.** `py -3.12 -m pytest -v` completo, capturas de accesos permitidos y denegados en `/docs`, y una consulta a la base que muestre hash, correo cifrado y seudónimo.
- [ ] **Step 3: Escribir el informe** con los seis entregables de la actividad. Debe citar resultados reales de las corridas, y la conclusión incluye la sección 8 de la spec.
- [ ] **Step 4: Verificar que no hay secretos.** `git grep -n -E "GM_[A-Z_]+_KEY=.+|GM_JWT_SECRET=.+"`. Esperado: sin valores reales.
- [ ] **Step 5: Commit y PR 5.** Exportar el informe al formato que pida Canvas.
