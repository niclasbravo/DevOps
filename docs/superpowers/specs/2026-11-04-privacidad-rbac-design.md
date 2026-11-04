# Diseño: minimización de datos, seudonimización y RBAC en GameMatch

Fecha: 2026-11-04 · Estado: en revisión · Cubre las Partes 1 a 3 de la actividad y define la Parte 4.

## 1. Objetivo y alcance

Reducir los datos personales que GameMatch guarda y garantizar que cada persona acceda solo a lo que necesita,
implementándolo en el código y demostrándolo con pruebas.

**Punto de partida real.** GameMatch no tiene base de datos, cuentas ni autenticación. Este documento no inventa un
esquema anterior: el "esquema original" es lo que existe en `main` (commit `080b430`, que incluye la página web del PR #13). Los campos que nunca existieron
y se evaluaron para el diseño se marcan como *evaluado*, nunca como eliminados.

**Fuera de alcance:** un frontend nuevo (solo se adapta la página existente), envío de correos, migrar a Postgres, Kubernetes, y el fallo del workflow
`entrenamiento.yml` del 2026-11-01 (no afecta a esta actividad).

## 2. Parte 1: esquema de datos

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

## 3. Parte 2: seudonimización y anonimización

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

## 4. Parte 3: RBAC

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

### 4.3 Matriz para el informe

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

## 5. Parte 4: implementación

### 5.1 Módulos nuevos en `backend/app/`

| Módulo | Responsabilidad |
|---|---|
| `db.py` | Conexión SQLite, esquema, triggers y datos iniciales de roles y permisos |
| `crypto.py` | Argon2id, HMAC de seudónimos, cifrado Fernet y lectura de claves |
| `auth.py` | Registro, login, JWT, `current_account`, `require_permission` |
| `audit.py` | Escritura de `audit_log` |
| `reports.py` | Agregación anónima con umbral |
| `logging_safe.py` | Filtro de redacción de logs |
| `errors.py` | Manejadores de error seguros |
| `cli.py` | `create-admin` y `migrate-feedback` |

`main.py` conserva la API existente y registra las rutas nuevas. Toda consulta SQL usa parámetros, sin concatenar
texto. Dependencias nuevas: `argon2-cffi`, `pyjwt`, `cryptography`. SQLite va con la biblioteca estándar de Python.

### 5.2 Contraseñas, sesiones y claves

- **Contraseñas:** Argon2id, de 12 a 128 caracteres. Se rehashean al iniciar sesión si cambian los parámetros.
- **Login:** el mismo mensaje y el mismo costo de verificación exista o no la cuenta (hash señuelo), para no revelar
  qué alias existen. Bloqueo de 15 minutos tras 5 intentos fallidos.
- **Tokens:** JWT HS256 con caducidad de 30 minutos y `sub` igual a `account.id`.
- **Claves faltantes (fail-closed):** la API arranca igual. Los endpoints públicos funcionan, pero todo lo que necesita
  claves responde 503, sin valores por defecto. Así `docker run` y el smoke test actual siguen funcionando sin secretos.
- **Claves débiles:** se rechazan las de menos de 32 bytes.
- **Rotación:** `GM_EMAIL_KEY` admite varias claves (`MultiFernet`). Rotar `GM_PSEUDONYM_KEY` exige recalcular
  seudónimos, y no se automatiza en esta entrega.

### 5.3 Errores y registros

- **Fuga detectada:** el 422 por defecto de FastAPI devuelve el campo `input`, que incluiría la contraseña enviada. El
  manejador propio devuelve solo el campo y el tipo de error, sin el valor.
- Las excepciones no controladas responden 500 con un mensaje genérico y un id de correlación; el detalle queda en el
  log, ya filtrado.
- `/stats` deja de exponer `commit`, `sha256`, `trained_at` y `data_file`, que pasan a `/admin/model`. Mantiene
  `recommender`, `catalog_size` y latencias, que es lo que leen el smoke test y `test_api.py`, así que **el smoke test
  obligatorio del CI no cambia**.

### 5.4 Despliegue

`GM_DB_PATH=/app/data/gamematch.db` reemplaza a `FEEDBACK_PATH`. Se documenta el uso de un volumen en `/app/data`:
sin él, los datos se pierden al borrar el contenedor. La imagen sigue corriendo sin root y no incluye claves.

### 5.5 Página web

`backend/app/static/index.html` (PR #13) vota con un `user_id` guardado en `localStorage`, lo que es incompatible con
este diseño. Se adapta así:

- Se elimina `gamematch-uid` y el campo `user_id` de la petición.
- Recomendar sigue siendo público. Votar exige iniciar sesión, y la página agrega un formulario de registro y login.
- El token se guarda solo en una variable de JavaScript, nunca en `localStorage`, `sessionStorage` ni cookies. Recargar
  la página cierra la sesión: se acepta esa incomodidad a cambio de que el token no persista en el navegador.
- Si `/feedback` responde 401, la página olvida el token y vuelve a pedir el login.
- El contenido dinámico se sigue escribiendo con `textContent`, sin `innerHTML`.

## 6. Pruebas

Cada celda de la matriz 4.3 tiene al menos una prueba. `tests/test_rbac.py` se parametriza desde una tabla de
`(rol, método, ruta, código esperado)`, para que la matriz y la evidencia no puedan divergir.

| Archivo | Comprueba |
|---|---|
| `test_rbac.py` | Accesos permitidos y denegados (401 y 403), y que las denegaciones quedan auditadas |
| `test_auth.py` | Registro sin escalar rol, login, bloqueo, token vencido o manipulado, suspensión inmediata, mismo error con alias inexistente |
| `test_privacidad.py` | Campos extra rechazados, hash Argon2id en la base, correo no legible en la base, seudónimo sin el id, fecha sin hora |
| `test_reportes.py` | Umbral de 5, sin seudónimos en el reporte |
| `test_logs_errores.py` | Sin contraseña en 422, sin datos personales en logs, 500 genérico |
| `test_db.py` | Triggers rechazan `UPDATE` y `DELETE` en `audit_log`; migración de JSONL; intento de inyección SQL |
| `test_claves.py` | Sin claves responde 503 y lo público sigue funcionando |
| `test_pagina.py` | La página no usa `localStorage` ni `user_id`, envía el token y no usa `innerHTML` |

Las pruebas existentes de `/feedback` y `/stats` se actualizan al nuevo contrato. El job `Build y pruebas (pytest)` ya
corre todo y sus resultados son la evidencia para el informe.

## 7. Entrega por etapas

Cada etapa: issue, rama desde `main`, PR con la plantilla, CI en verde y aprobación de otro integrante.

| PR | Rama | Contenido |
|---|---|---|
| 1 | `docs/diseno-privacidad-rbac` | Este documento |
| 2 | `feature/bd-minimizacion` | `db.py`, `crypto.py`, `feedback` en SQLite con seudónimo, migración, validaciones |
| 3 | `feature/auth-rbac` | `auth.py`, `audit.py`, `cli.py`, permisos en endpoints, `/stats` reducido, login en la página |
| 4 | `feature/anonimizacion-logs` | `reports.py`, `logging_safe.py`, `errors.py` |
| 5 | `docs/informe-actividad` | Evidencias, README e informe para Canvas |

Entre el PR 2 y el PR 3, `POST /feedback` sigue aceptando `user_id`, ya seudonimizado al guardar; el PR 3 lo reemplaza
por la identidad del token.

## 8. Limitaciones y riesgos que quedan

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
