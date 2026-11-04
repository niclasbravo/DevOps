# Informe técnico: Actividad 6, privacidad y control de acceso en GameMatch

**Asignatura:** DevOps (Sec/ML)

**Integrantes:** Nicolas Bravo, Maximiliano Mella, Martín Lorca

**Repositorio:** https://github.com/niclasbravo/DevOps

**Fecha:** 4 de noviembre de 2026

## 1. Resumen

GameMatch es una API FastAPI, con una página web, que recomienda videojuegos indie. Al iniciar esta actividad no tenía base de datos, cuentas ni autenticación: todos los endpoints eran públicos y el único dato ligado a personas era `data/feedback.jsonl`.

Por eso el esquema original de este informe es lo que existía realmente en `main`, y no un esquema inventado. Los campos que nunca existieron y se evaluaron para el diseño aparecen marcados como *evaluado*, nunca como eliminados.

El trabajo agregó una base SQLite mínima, votos seudonimizados, reportes anónimos, autenticación, RBAC con tres roles, auditoría de solo inserción y manejo seguro de errores y logs. Todo se trabajó mediante ramas, Pull Requests, CI y revisión de otro integrante.

## 2. Parte 1: esquema de base de datos rediseñado

### 2.1 Esquema original

| Almacén | Campos | Observaciones |
|---|---|---|
| `data/feedback.jsonl` | `ts`, `user_id`, `game`, `vote` | Único dato ligado a personas. `ts` tiene precisión de segundos. `user_id` lo envía el cliente sin verificación. |
| `CATALOG` (memoria) | `title`, `tags` | Sin datos personales. |
| `models/gamematch.joblib` | Vectores y datos públicos de IGDB | Sin datos personales. |
| `models/model_meta.json` | `commit`, `sha256`, `trained_at`, `data_file`, métricas | Sin datos personales; información interna. |
| `_latencies` (memoria) | Latencias en ms | Sin datos personales. |
| `localStorage` del navegador | `gamematch-uid` | Id persistente generado con `Math.random` en `backend/app/static/index.html`. Se envía como `user_id` en cada voto. |

Problemas: todos los endpoints son públicos, cualquiera puede escribir votos con cualquier `user_id`, `/stats` expone
datos internos del modelo, y el código llama "anónimo" a un identificador estable, que en realidad es un seudónimo.

### 2.2 Esquema rediseñado (SQLite, `GM_DB_PATH`)

```sql
CREATE TABLE roles (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE CHECK (name IN ('admin', 'analista', 'usuario'))
);
CREATE TABLE permissions (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE
);
CREATE TABLE role_permissions (
  role_id INTEGER NOT NULL REFERENCES roles(id),
  permission_id INTEGER NOT NULL REFERENCES permissions(id),
  PRIMARY KEY (role_id, permission_id)
);
CREATE TABLE accounts (
  id TEXT PRIMARY KEY,                    -- UUID aleatorio, sin significado
  alias TEXT NOT NULL UNIQUE COLLATE NOCASE, -- no tiene que ser el nombre real
  password_hash TEXT NOT NULL,            -- Argon2id
  email_encrypted BLOB,                   -- opcional; cifrado con Fernet
  role_id INTEGER NOT NULL REFERENCES roles(id),
  is_active INTEGER NOT NULL DEFAULT 1,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_on TEXT NOT NULL                -- solo fecha
);
CREATE TABLE feedback (
  id INTEGER PRIMARY KEY,
  pseudonym TEXT NOT NULL,                -- HMAC-SHA256(account.id); sin clave foránea a accounts
  game TEXT NOT NULL,
  vote INTEGER NOT NULL CHECK (vote IN (1, -1)),
  voted_on TEXT NOT NULL,                 -- solo fecha
  UNIQUE (pseudonym, game)
);
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  actor_pseudonym TEXT,                   -- NULL si la petición no tiene sesión
  action TEXT NOT NULL,
  target_pseudonym TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ('permitido', 'denegado'))
);
-- audit_log solo admite inserciones
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log es de solo inserción'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log es de solo inserción'); END;
```

`feedback` no tiene clave foránea hacia `accounts` a propósito: un acceso a esa tabla no permite unir votos con
cuentas sin la clave del HMAC.

### 2.3 Tabla de decisiones

| Tabla y campo | Decisión | Justificación y riesgo de conservarlo |
|---|---|---|
| `feedback.jsonl`: `user_id` | Reducir (se reemplaza por `feedback.pseudonym`) | El voto solo necesita evitar duplicados. Un id estable elegido por el cliente puede ser un nombre o correo real. |
| `localStorage`: `gamematch-uid` | Eliminar | Con cuentas ya no se necesita. Es un identificador persistente del navegador que permite seguir a una persona entre visitas, y `Math.random` no es criptográficamente seguro. |
| `feedback.jsonl`: `ts` | Reducir a fecha (`voted_on`) | Basta el día para analizar tendencias. La hora exacta facilita cruzar la actividad con otras fuentes. |
| `feedback.jsonl`: `game`, `vote` | Mantener | Son el propósito del voto. |
| `CATALOG`, modelo | Mantener | Sin datos personales. |
| `model_meta`: `commit`, `sha256`, `data_file` en `/stats` | Reducir exposición | Se usan para operar, no para el público. Pasan a una ruta administrativa. |
| `accounts.id` | Agregar | Identificador interno aleatorio, sin datos de la persona. |
| `accounts.alias` | Agregar | Necesario para iniciar sesión. Se valida con un patrón que rechaza correos. |
| `accounts.password_hash` | Agregar, protegido | Necesario para autenticar. Solo Argon2id, nunca texto plano. |
| `accounts.email_encrypted` | Opcional, cifrado | Solo para recuperación manual de la cuenta. El sistema funciona sin él. Un correo en claro expondría identidades en caso de filtración. |
| `accounts.created_on` | Reducir a fecha | Basta para la administración de cuentas. |
| Nombre y apellido (*evaluado*) | No recolectar | Ninguna función los usa. Son identificadores directos. |
| Fecha de nacimiento (*evaluado*) | No recolectar | La recomendación no depende de la edad. Alternativa si el catálogo lo exigiera: guardar solo el resultado de una verificación de mayoría de edad. |
| Dirección IP (*evaluado*) | No recolectar ni registrar | El bloqueo de intentos se hace por cuenta. La IP es un dato personal y permite ubicar a la persona. |
| Teléfono, dirección, país (*evaluado*) | No recolectar | Ninguna función los usa. |
| Historial de consultas a `/recommendations` (*evaluado*) | No recolectar | Revela gustos y no se necesita: el modelo no aprende por usuario. |

### 2.4 Validaciones contra datos innecesarios

Los modelos Pydantic de entrada usan `extra="forbid"`: un campo no definido, como `birthdate`, se rechaza con 422 y
nunca llega a la base. `alias` acepta solo `^[A-Za-z0-9_-]{3,30}$`, y `POST /feedback` deja de aceptar `user_id`: la
identidad sale del token.

## 3. Parte 2: documento de seudonimización y anonimización

**Seudonimización:** el dato sigue siendo personal, porque con información adicional se puede reidentificar.
**Anonimización:** no se puede identificar razonablemente a nadie, ni cruzando con otras fuentes.

| Dónde | Técnica | Implementación | Protege | Por qué esta técnica |
|---|---|---|---|---|
| `feedback.pseudonym` | Seudonimización | `HMAC-SHA256(GM_PSEUDONYM_KEY, account.id)` | La relación entre persona y gustos | La persona debe poder ver y cambiar sus votos, y se deben evitar duplicados. Anonimizar lo impediría. Un hash sin clave se podría recalcular. |
| `audit_log.actor_pseudonym` y `target_pseudonym` | Seudonimización | Mismo HMAC | La identidad en la auditoría | Una investigación debe poder atribuir acciones, pero leer la auditoría no debe revelar quién es quién. |
| `GET /reports/feedback` | Anonimización | Totales por juego, sin seudónimos ni fechas; se omiten los juegos con menos de 5 votos | Votantes individuales en juegos poco votados | El análisis solo necesita agregados. El umbral evita que un voto único señale a alguien. |
| Reentrenamiento (uso futuro de votos) | Anonimización | Solo consume la salida agregada de ese reporte | Evita que datos personales entren al modelo y a los artefactos de CI | Hoy el entrenamiento no usa votos; esto fija la regla si se incorporan. |
| Migración de `feedback.jsonl` | Seudonimización y reducción | `user_id` → HMAC de `legacy:<user_id>`; `ts` → fecha; luego se borra el archivo | Votos ya guardados | Conserva los votos sin mantener el id original. |

**Ejemplo.** Hoy: `{"ts":"2026-10-30T14:03:22+00:00","user_id":"nico-b","game":"Hades","vote":1}`.
Con el rediseño: `pseudonym=9f2c…e1, game=Hades, vote=1, voted_on=2026-10-30`.
En el reporte: `{"game":"Hades","votos":37,"positivos":30}`, sin rastro de la persona.

**Eliminación de cuenta.** `DELETE /me` borra la fila de `accounts`. El HMAC necesita el `id`, que ya no existe, así que
los votos no se pueden volver a vincular por la vía del sistema y siguen sumando a los totales.

**Reversión de la seudonimización.** Revertir es calcular el HMAC de un `account.id` y comparar. Por eso:

- `GM_PSEUDONYM_KEY` vive solo en variables de entorno o en el gestor de secretos. Nunca en el repo, la imagen, la
  base ni los logs, y ningún endpoint la devuelve.
- Cada clave tiene un solo propósito: `GM_PSEUDONYM_KEY`, `GM_EMAIL_KEY` y `GM_JWT_SECRET` son distintas.
- El rol analista no tiene acceso a seudónimos ni a cuentas, y por lo tanto no puede vincularlos.
- Ver el correo de una cuenta exige `accounts:reveal_email` y queda registrado en `audit_log`.

**Registros, reportes, pruebas y desarrollo.**

- **Logs:** se registran evento y resultado; nunca alias, correos, tokens, contraseñas ni cuerpos de peticiones. Un
  filtro de logging redacta además patrones de correo y de tokens, como segunda barrera.
- **Reportes:** solo datos agregados y con umbral.
- **Pruebas:** datos sintéticos generados en los fixtures, y claves de prueba generadas en cada corrida.
- **Desarrollo:** `.env` sigue fuera de git y la base está en `backend/data/`, ya ignorada. Nunca se copian datos reales.
- **Artefactos de CI:** ni la base ni los votos se publican como artefacto.

## 4. Parte 3: matriz RBAC

### 4.1 Roles

| Rol | Responsabilidad | Mínimo privilegio |
|---|---|---|
| `usuario` | Votar y gestionar sus propios datos | Solo sus datos |
| `analista` (personal autorizado) | Analizar qué juegos funcionan | Solo agregados anónimos y métricas del servicio |
| `admin` | Operar el sistema | Gestiona cuentas y auditoría, pero no puede ver secretos ni contraseñas |

Una petición sin token solo accede a los endpoints públicos. El registro siempre crea cuentas `usuario`: la API no
acepta un campo de rol en ese endpoint.

### 4.2 Permisos y endpoints

| Permiso | Endpoint | usuario | analista | admin |
|---|---|---|---|---|
| Público | `GET /`, `/health`, `/games`, `/stats` (reducido); `POST /recommendations`, `/auth/register`, `/auth/login` | Sí | Sí | Sí |
| `feedback:write` | `POST /feedback` | Sí | No | No |
| `me:read` | `GET /me`, `GET /me/feedback` | Sí | Sí | Sí |
| `me:update` | `PATCH /me` (contraseña y correo propios; exige la contraseña actual) | Sí | Sí | Sí |
| `me:delete` | `DELETE /me` | Sí | Sí | Sí, salvo el último admin |
| `reports:read` | `GET /reports/feedback` | No | Sí | Sí |
| `model:read` | `GET /admin/model` | No | No | Sí |
| `accounts:read` | `GET /admin/accounts` (sin correo ni hash) | No | No | Sí |
| `accounts:manage` | `PATCH /admin/accounts/{id}/role`, `/status` | No | No | Sí |
| `accounts:reveal_email` | `POST /admin/accounts/{id}/reveal-email` | No | No | Sí, auditado |
| `audit:read` | `GET /admin/audit` | No | No | Sí |

### 4.3 Matriz de permisos

| Acción o recurso | Administrador | Analista | Usuario final |
|---|---|---|---|
| Gestionar roles y permisos | Permitido, nunca sobre sí mismo | Denegado | Denegado |
| Consultar datos propios | Permitido | Permitido | Permitido |
| Consultar datos de otros usuarios | Solo alias, rol y estado | Denegado | Denegado |
| Ver el correo de otra cuenta | Solo con permiso explícito y auditoría | Denegado | Denegado |
| Modificar información sensible | Solo rol y estado de cuentas | Denegado | Solo la propia |
| Ver votos individuales | Denegado | Denegado | Solo los propios |
| Ver reportes agregados | Permitido | Permitido | Denegado |
| Ver auditoría | Permitido | Denegado | Denegado |
| Modificar o borrar auditoría | Denegado (lo impide un trigger) | Denegado | Denegado |
| Ver contraseñas | Denegado (solo existe el hash, y ninguna API lo devuelve) | Denegado | Denegado |
| Acceder a claves criptográficas | Denegado por API; solo el proceso las usa | Denegado | Denegado |

### 4.4 Control de acceso a datos sensibles

- La autorización se aplica en el servidor con una dependencia `require_permission("...")` por endpoint. Lo que no
  tiene permiso explícito queda denegado.
- El rol y los permisos se leen de la base en cada petición, no del token. Un cambio de rol o una suspensión rige de
  inmediato.
- Toda denegación por permisos y toda operación administrativa se registran en `audit_log`.
- Un admin no puede cambiar su propio rol ni dejar el sistema sin administradores.
- El primer admin se crea por CLI (`python -m app.cli create-admin`), nunca por la API.

## 5. Parte 4: implementación en el código

### 5.1 Entrega por etapas

El cambio se dividió en Pull Requests revisados, siguiendo el flujo del equipo. La rama `main` exige un PR, una aprobación y que pasen los checks de CI, sin excepciones para el administrador del repositorio.

| PR | Contenido |
|---|---|
| [#15](https://github.com/niclasbravo/DevOps/pull/15) | Diseño y plan |
| [#17](https://github.com/niclasbravo/DevOps/pull/17) | SQLite, seudónimos, cifrado y minimización |
| [#19](https://github.com/niclasbravo/DevOps/pull/19) | Autenticación, RBAC, auditoría y login en la página |
| [#21](https://github.com/niclasbravo/DevOps/pull/21) | Reportes anónimos, errores y logs seguros |

### 5.2 Módulos

| Módulo | Responsabilidad |
|---|---|
| `app/crypto.py` | HMAC de seudónimos, Argon2id, cifrado Fernet y lectura de claves |
| `app/db.py` | Esquema, triggers de auditoría, roles y permisos, y transacciones de escritura |
| `app/auth.py` | Registro, login, JWT, bloqueo de intentos y `require_permission` |
| `app/audit.py` | Registro de auditoría seudonimizado |
| `app/me.py` | Datos de la propia cuenta |
| `app/admin.py` | Operaciones administrativas |
| `app/reports.py` | Reporte anónimo con umbral |
| `app/errors.py` | Respuestas de error seguras |
| `app/logging_safe.py` | Redacción de logs |
| `app/cli.py` | Creación del primer administrador y migración de votos |
| `app/static/index.html` | Página con login; el token vive solo en memoria |

### 5.3 Cómo funciona cada control

**Minimización.** Los modelos de entrada usan `extra="forbid"`, así que un campo no previsto se rechaza con 422 antes de procesarse. El voto guarda el título del catálogo, y solo la fecha.

```python
class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(pattern=ALIAS_PATTERN, examples=["jugador_1"])
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN)
```

**Seudonimización.** El voto guarda un HMAC-SHA256 del id de la cuenta. Sin la clave no se puede recalcular ni comprobar a quién corresponde.

```python
def pseudonymize(value: str) -> str:
    return hmac.new(get_key("GM_PSEUDONYM_KEY"), value.encode("utf-8"), hashlib.sha256).hexdigest()
```

**Contraseñas y claves.** Las contraseñas se guardan con Argon2id. Las claves solo se leen de variables de entorno, sin valores por defecto, y deben tener al menos 32 bytes. Si falta una, lo que la necesita responde 503 y lo público sigue funcionando.

**Autorización.** Cada endpoint protegido declara su permiso. El rol y los permisos se leen de la base en cada petición, por lo que un cambio de rol o una suspensión rigen de inmediato. Cada rechazo queda en la auditoría.

```python
@router.get("/audit")
def read_audit(..., _: sqlite3.Row = Depends(require_permission("audit:read"))) -> list[dict]:
```

**Restricciones en la base.** SQLite no tiene usuarios propios, así que la base impone lo que puede: valores válidos con `CHECK`, un voto por persona y juego con `UNIQUE`, y auditoría de solo inserción con triggers. Las operaciones críticas usan `BEGIN IMMEDIATE`, y la fila de auditoría entra en la misma transacción: si no se puede auditar, la operación no se aplica.

**Errores y logs.** El error 422 de FastAPI devolvía la contraseña enviada en el campo `input`. El manejador propio devuelve solo campo y tipo de error. El 500 responde un mensaje genérico con un `error_id`. Los logs redactan correos, tokens e ids, y se apagó el log de acceso de uvicorn porque registra la IP de cada cliente.

## 6. Evidencias de pruebas

### 6.1 Suite automatizada

Resultado de `pytest` con todas las etapas integradas: **256 pruebas pasan**, y `ruff` no reporta errores. El proyecto tenía 40 pruebas al iniciar.

| Archivo | Pruebas | Comprueba |
|---|---|---|
| `test_rbac.py` | 90 | Matriz de permisos, reglas de administración y concurrencia |
| `test_auth.py` | 48 | Registro, login, bloqueo, tokens y datos propios |
| `test_privacidad.py` | 25 | Minimización, seudónimos y migración |
| `test_logs_errores.py` | 15 | Errores y logs sin datos sensibles, inyección SQL |
| `test_db.py` | 12 | Reglas de la base y auditoría de solo inserción |
| `test_crypto.py` | 10 | Seudónimos, Argon2id, cifrado y claves |
| `test_pagina.py` | 10 | La página no guarda identificadores |
| `test_reportes.py` | 7 | Umbral y anonimato del reporte |
| Pruebas previas del proyecto | 39 | API, modelo, ML y recomendador |

La matriz RBAC se comprueba con 68 casos parametrizados: 40 accesos permitidos, 15 rechazados con 403, 12 rechazados con 401 y 1 rechazado con 409 por la regla del último administrador. Una prueba adicional falla si aparece un método o una ruta sin fila en la matriz.

### 6.2 Casos de acceso permitido y denegado

Resultados obtenidos sobre la imagen Docker real, construida desde el código, con claves y volumen de prueba.

| Caso | Resultado esperado | Resultado obtenido |
|---|---|---|
| Registrar una cuenta | Permitido | 201 |
| Votar sin token | Denegado | 401 |
| Votar con sesión de `usuario` | Permitido | 201 |
| `GET /admin/accounts` como `usuario` | Denegado | 403 |
| `GET /reports/feedback` como `usuario` | Denegado | 403 |
| `/stats` expone datos del modelo | No | No |
| Proceso del contenedor | Sin root | uid 10001 |
| Voto tras recrear el contenedor con volumen | Se conserva | 1 voto |

Sin claves configuradas:

| Caso | Resultado esperado | Resultado obtenido |
|---|---|---|
| `GET /health`, `GET /`, `POST /recommendations` | Funcionan | 200 |
| `POST /auth/login` | No disponible | 503 |
| `POST /feedback` con token | No disponible | 503 |

### 6.3 Comprobaciones de protección de datos

Contenido real de la base tras las pruebas en Docker:

| Dato | Valor guardado |
|---|---|
| Contraseña | `$argon2id$v=19$m=65536,t=3,p=4...` |
| Fecha de creación | `2026-11-04`, sin hora |
| Voto | `477050b87dae06e0...`, juego, voto y fecha, sin id de cuenta |
| Rechazos en auditoría | 3 filas, sin alias |

Además:

- El log del contenedor no contiene alias ni direcciones IP.
- En un servidor uvicorn real, un 422 con un dato secreto no lo devolvió ni lo registró.
- En el navegador, tras iniciar sesión y votar, `localStorage`, `sessionStorage` y las cookies quedan vacíos, y recargar cierra la sesión.
- Con un token inválido, la página vuelve a pedir el login.

### 6.4 Revisión independiente

Antes de cerrar, una revisión independiente examinó todo el código. No encontró bypass de autorización ni escalada de privilegios, pero sí 8 hallazgos importantes. Todos se corrigieron con una prueba que primero falló.

| Hallazgo | Evidencia | Corrección |
|---|---|---|
| Variantes del título creaban votos duplicados | `Hades` y `hades` eran dos filas | Se guarda el título del catálogo |
| Intentos concurrentes evadían el bloqueo de login | Bloqueo tras 80 intentos en vez de 5 | Contador atómico en SQL |
| Dos admins podían eliminarse a la vez | Hasta 23 de 30 corridas sin admins | Transacción con `BEGIN IMMEDIATE`, también en cambios de rol |
| Una línea dañada abortaba la migración | 7 formas de línea corrupta | Lectura en bytes, línea por línea |
| La migración guardaba datos inválidos | Un correo quedaba como fecha | Se valida fecha, juego y voto |
| Operaciones aplicadas sin auditoría | Rol cambiado con respuesta 503 | Auditoría en la misma transacción |
| `create-admin` imprimía la contraseña rechazada | Visible en la salida de error | Se muestra solo campo y tipo |
| La prueba de cobertura no veía los métodos | `PATCH` y `DELETE /me` sin cubrir | Compara método y ruta |

Quedaron registrados como mejoras menores, sin corregir:

- `GET /admin/audit` y `GET /admin/model` no se auditan.
- Un administrador puede suspenderse a sí mismo si existe otro.
- La migración borra el archivo aunque omita todas sus líneas.
- Un `offset` gigante en las rutas administrativas responde 500.
- `PATCH /me` no cuenta intentos fallidos.
- No se exige que las tres claves sean distintas.

## 7. Conclusión técnica

### 7.1 Mejoras alcanzadas

- GameMatch pasó de no tener control de acceso a tener autenticación y tres roles con mínimo privilegio, comprobados en cada PR.
- El sistema recolecta lo mínimo: alias, contraseña protegida y voto. Nombre, fecha de nacimiento e IP nunca se guardan.
- El dato que el código llamaba "anónimo" se reconoció como seudónimo y ahora se protege como tal.
- Se eliminó un identificador persistente en el navegador y una fuga de contraseñas en los errores.
- Las operaciones sensibles quedan auditadas sin exponer identidades.

### 7.2 Limitaciones y riesgos que deben mitigarse

- **SQLite no tiene usuarios ni permisos propios.** El control de acceso está en el servidor; a nivel de base solo hay
  restricciones `CHECK` y triggers. Quien acceda al archivo con permisos de escritura puede eliminar los triggers.
  Postgres permitiría roles y `GRANT` reales.
- **Datos en disco sin cifrar por completo.** Solo el correo está cifrado; el resto depende de los permisos del
  sistema y del cifrado del volumen.
- **Claves en variables de entorno.** Es adecuado para el proyecto; en producción convendría un gestor de secretos.
- **Seudonimizar no es anonimizar.** Quien tenga la clave y los `account.id` puede vincular votos.
- **Umbral de 5.** Reduce el riesgo, pero restar reportes de fechas distintas podría revelar votos individuales.
- **Sin revocación de JWT.** Un token robado sirve hasta 30 minutos para las acciones de su rol, salvo que se suspenda
  la cuenta.
- **Sin retención automática.** `audit_log` crece sin límite: los triggers impiden purgar, y el plazo y mecanismo de
  retención quedan por definir.
- **Token en memoria del navegador.** Evita que persista, pero un script malicioso en la página podría leerlo mientras
  la sesión está abierta. Conviene agregar una política CSP en una entrega futura.
- **Recuperación de cuenta manual.** No hay envío de correos. Si esta función no se usa, lo coherente con la
  minimización es eliminar el campo de correo.

Además, cualquiera puede bloquear una cuenta por 15 minutos con intentos fallidos, porque el bloqueo es por cuenta y no por IP, y `/auth/register` y `/auth/login` no tienen límite de tasa. Las peticiones sin sesión a rutas protegidas también agregan filas a la auditoría, lo que permite inflarla.

## 8. Referencias

- OWASP, *Password Storage Cheat Sheet*.
- OWASP, *Logging Cheat Sheet*.
- RFC 9106, *Argon2 Memory-Hard Function for Password Hashing*.
- RFC 7519, *JSON Web Token (JWT)*.
- RFC 2104, *HMAC: Keyed-Hashing for Message Authentication*.
- NIST, *Role-Based Access Control* (ANSI INCITS 359).
- Reglamento (UE) 2016/679 (RGPD), artículo 4, definición de seudonimización, y considerando 26, sobre datos anónimos.
