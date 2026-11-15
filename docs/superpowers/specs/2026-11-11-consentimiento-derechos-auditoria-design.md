# Diseño: consentimiento, ejercicio de derechos, auditoría y respuesta a incidentes en GameMatch

Fecha: 2026-11-11 · Estado: en revisión · Hito 3 · Entrega: 2026-11-15

## 1. Objetivo y alcance

Agregar a GameMatch los mecanismos para gestionar el consentimiento por finalidad, atender solicitudes de acceso,
portabilidad, rectificación y supresión, reconstruir lo ocurrido con un registro de auditoría íntegro, y detectar y
contener incidentes. Todo queda implementado en el código y demostrado con pruebas.

**Punto de partida real** (`main`, commit `188ec08`, Hito 2): cuentas con Argon2id, RBAC por permisos leídos de la base,
votos seudonimizados con HMAC, correo cifrado con Fernet, `audit_log` de solo inserción y 156 pruebas en CI.
Este diseño extiende esos módulos; no los reemplaza.

**Marco normativo.** Ley 19.628 modificada por la Ley 21.719. La fecha legal de vigencia es el 1 de diciembre de 2026, y
hay un proyecto en el Senado que la posterga un año. El diseño no depende de esa fecha. Se usan solo estas reglas,
verificadas en fuentes:

| Regla | Origen | Cómo la usa el diseño |
|---|---|---|
| Menores de 14 años: el consentimiento lo dan sus padres o representantes legales | Art. 16 quáter | Flujo diferenciado de la sección 3.5 |
| De 14 a 17 años: consienten como adultos, salvo datos sensibles si tienen menos de 16 | Art. 16 quáter | GameMatch no trata datos sensibles, así que no se distingue este tramo |
| Responder las solicitudes de derechos dentro de 30 días corridos, prorrogables una vez por 30 | Art. 11 | Campo `due_on` y prórroga única en la sección 4 |
| Reportar a la Agencia las vulneraciones con riesgo razonable "por los medios más expeditos posibles y sin dilaciones indebidas" | Art. 14 sexies | Plan de incidentes, sección 7. La ley no fija horas |
| Avisar también a los titulares si la vulneración afecta datos sensibles, de menores de 14 o económicos | Art. 14 sexies | Por eso el sistema debe saber qué cuentas son de menores de 14 |

La Ley 21.663 (Marco de Ciberseguridad) obliga a servicios esenciales y operadores de importancia vital. GameMatch no
es ninguno de los dos, así que sus plazos (alerta en 3 horas) no se aplican; el plan lo dice expresamente.

**Fuera de alcance:** envío real de correos (se simula con un buzón local, ver 8), Postgres, Kubernetes, panel
administrativo visual (la administración es por API y CLI), y datos sensibles.

## 2. Bases de licitud por tratamiento

El consentimiento no es la base de todo. Pedirlo para lo que el servicio necesita sí o sí sería un consentimiento
condicionado, que la ley exige que sea libre.

| Tratamiento | Datos | Base | Si la persona no la otorga o la retira |
|---|---|---|---|
| Cuenta e inicio de sesión | `alias`, `password_hash`, `is_child` | Ejecución del servicio que la persona pide | No hay cuenta |
| Guardar y mostrar sus votos | `feedback` (seudónimo, juego, voto, fecha) | Ejecución del servicio | No puede votar |
| Seguridad y auditoría | `audit_log`, `security_alerts` | Deber de seguridad del responsable | No aplica: no se puede retirar |
| Correo de recuperación | `email_encrypted` | **Consentimiento** `correo_recuperacion` | No se guarda correo; al revocar se borra |
| Usar sus votos en reportes y en un reentrenamiento futuro | `feedback` agregado | **Consentimiento** `votos_analitica` | Sus votos quedan fuera de reportes y modelos |
| Recibir novedades por correo | `email_encrypted` | **Consentimiento** `novedades_correo` | No recibe novedades |
| Toda la cuenta de un menor de 14 | Todo lo anterior | **Consentimiento del representante** | La cuenta no se activa |

## 3. Parte 1: módulo de consentimiento

### 3.1 Finalidades y versiones

Las finalidades viven en la tabla `consent_purposes`, sembrada desde el código. Cada texto tiene versión; un cambio
material del texto sube la versión.

| `code` | Texto informado (resumen) | Versión inicial | Para menores de 14 |
|---|---|---|---|
| `correo_recuperacion` | Guardamos tu correo cifrado solo para ayudarte a recuperar la cuenta. No lo usamos para nada más. | `1` | Solo si lo otorga el representante |
| `votos_analitica` | Tus votos se suman, sin identificarte, a reportes de qué juegos gustan y a futuros reentrenamientos del modelo. | `1` | Solo si lo otorga el representante |
| `novedades_correo` | Te escribimos con novedades del catálogo. | `1` | No disponible |

`novedades_correo` no se ofrece a menores de 14 porque es comunicación comercial y no aporta al servicio.

### 3.2 Modelo de datos

```sql
CREATE TABLE consent_purposes (
  code TEXT PRIMARY KEY,
  version TEXT NOT NULL,          -- versión vigente del texto
  text TEXT NOT NULL,
  allowed_for_children INTEGER NOT NULL
);
CREATE TABLE consent_purpose_versions (   -- textos anteriores: permiten saber qué se aceptó
  code TEXT NOT NULL REFERENCES consent_purposes(code),
  version TEXT NOT NULL,
  text TEXT NOT NULL,
  published_on TEXT NOT NULL,
  PRIMARY KEY (code, version)
);
CREATE TABLE consent_events (
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,          -- HMAC(account.id): mismo seudónimo que feedback
  purpose TEXT NOT NULL REFERENCES consent_purposes(code),
  text_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('otorgado', 'revocado')),
  actor TEXT NOT NULL CHECK (actor IN ('titular', 'representante', 'sistema')),
  ts TEXT NOT NULL                -- fecha y hora UTC, en segundos
);
CREATE INDEX consent_events_subject ON consent_events (subject, purpose, id);
CREATE TRIGGER consent_no_update BEFORE UPDATE ON consent_events
BEGIN SELECT RAISE(ABORT, 'consent_events es de solo inserción'); END;
CREATE TRIGGER consent_no_delete BEFORE DELETE ON consent_events
WHEN old.ts > strftime('%Y-%m-%dT%H:%M:%S', 'now', '-365 days')
BEGIN SELECT RAISE(ABORT, 'consent_events se conserva 365 días'); END;
```

**Decisiones:**

- **Eventos, no un estado que se sobrescribe.** El estado actual es el último evento de cada `(subject, purpose)`. El
  historial y la fecha de revocación salen de la misma tabla, y un trigger impide reescribirlos.
- **`subject` es el seudónimo, sin clave foránea a `accounts`.** Igual que `feedback`: así el reporte puede filtrar
  votos por consentimiento sin unir con las cuentas.
- **Se guarda la versión, no una copia del texto por evento.** El texto de cada versión está una sola vez en
  `consent_purpose_versions`.
- **Lo que no se guarda:** IP, navegador, ubicación o la hora con más precisión que segundos. No hacen falta para
  probar quién otorgó qué y cuándo.
- **`actor = 'sistema'`** solo para las revocaciones automáticas al suprimir una cuenta.

### 3.3 Reglas

- **Un consentimiento solo vale para su finalidad y su versión vigente.** `has_consent(subject, purpose)` es la única
  función que lo responde, y devuelve verdadero solo si el último evento es `otorgado` **y** su `text_version` es la
  vigente. Si el texto cambia de versión, el consentimiento anterior deja de valer hasta que se renueve.
- **Nada se marca por defecto.** El registro envía la lista de finalidades aceptadas; una finalidad ausente no se
  otorga.
- **Ninguna finalidad implica otra.** Cada proceso consulta `has_consent` con su propio código. Tener un correo guardado
  para recuperación no permite enviar novedades: el proceso de novedades exige `novedades_correo` **y** un correo
  guardado, y una prueba lo comprueba.
- **Correo y consentimiento van juntos.** Enviar `email` sin otorgar `correo_recuperacion` se rechaza con 400.
- **Un menor de 14 no puede otorgar consentimientos.** Sí puede revocarlos: retirar siempre lo protege.
- **Revocar es tan fácil como otorgar:** una petición, sin pedir contraseña.

### 3.4 Efecto de la revocación en los procesos

| Finalidad | Proceso que depende de ella | Efecto de revocar |
|---|---|---|
| `correo_recuperacion` | Recuperación manual (`reveal-email`) | En la misma transacción se borra `email_encrypted`. `reveal-email` devuelve `null` |
| `votos_analitica` | `GET /reports/feedback` y cualquier reentrenamiento | La consulta del reporte solo cuenta seudónimos con consentimiento vigente. Desde la revocación, sus votos dejan de sumar. El voto sigue guardado para la persona |
| `novedades_correo` | `python -m app.cli newsletter` (envío simulado) | La lista de destinatarios se calcula en cada envío, así que la revocación rige en el siguiente |

El filtro está en la consulta SQL, no en la página: ningún cliente puede saltárselo.

Los votos migrados del sistema antiguo (`legacy:`) no tienen consentimiento y quedan fuera de los reportes. Se acepta
esa pérdida.

### 3.5 Flujo diferenciado para menores de 14

1. El formulario de registro pregunta "¿Tienes menos de 14 años?". No pide fecha de nacimiento ni edad: solo se guarda
   `is_child`, que es lo que cambia las reglas.
2. Si la respuesta es sí, se exige el correo del representante. La cuenta se crea con `is_active = 0` y
   `pending_guardian = 1`, y no puede iniciar sesión.
3. El sistema genera un token aleatorio de 32 bytes, guarda solo su SHA-256 con vencimiento de 72 horas en
   `guardian_verifications`, y envía al representante un enlace a `/guardian.html#<token>`. El correo del
   representante **no se guarda** en la base.
4. La página del representante muestra qué datos tratará GameMatch y las finalidades permitidas para menores. Para
   activar la cuenta, el representante declara que es padre, madre o representante legal y elige finalidades.
5. `POST /guardian/confirm` activa la cuenta, registra cada consentimiento con `actor = 'representante'` y borra el
   token. Un token vencido, usado o inexistente responde lo mismo: "enlace inválido o vencido".
6. Si nadie confirma en 72 horas, `python -m app.cli purge-retention` borra la cuenta pendiente.

**Reglas de negocio:**

- Un admin no puede activar con `PATCH /admin/accounts/{id}/status` una cuenta con `pending_guardian = 1`.
- `is_child` no se puede rectificar por autoservicio: esa solicitud queda `en_evaluacion` (ver 4.4). Si no, bastaría
  rectificar la edad para saltarse al representante.
- Si una vulneración afecta cuentas con `is_child = 1`, el plan exige avisar a esos titulares (art. 14 sexies).

**Verificación y su límite.** El control es la posesión del correo y una declaración. Un menor podría escribir un
correo propio. Se acepta porque los datos tratados son de bajo riesgo (alias y votos de juegos) y porque una
verificación más fuerte (documento de identidad, pago con tarjeta) exigiría recolectar más datos de los que se protegen.
Queda registrado como limitación.

```sql
ALTER TABLE accounts ADD COLUMN is_child INTEGER NOT NULL DEFAULT 0;
ALTER TABLE accounts ADD COLUMN pending_guardian INTEGER NOT NULL DEFAULT 0;
CREATE TABLE guardian_verifications (
  account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL
);
```

### 3.6 API y pantallas

| Endpoint | Permiso | Uso |
|---|---|---|
| `GET /consents/purposes` | Público | Finalidades vigentes, texto y versión |
| `GET /me/consents` | `consent:manage` | Estado actual por finalidad |
| `GET /me/consents/history` | `consent:manage` | Todos los eventos propios |
| `POST /me/consents/{purpose}` | `consent:manage` | Otorgar la versión vigente |
| `DELETE /me/consents/{purpose}` | `consent:manage` | Revocar |
| `POST /auth/register` | Público | Agrega `is_child`, `guardian_email` y `consents` |
| `POST /guardian/confirm` | Público, exige token | Confirmación del representante |

Pantallas: el registro de `index.html` agrega la pregunta de edad y una casilla por finalidad, sin marcar. Un panel
"Mi privacidad" muestra el estado de cada finalidad con su versión, botones de otorgar y revocar, y el historial. Se
agrega `static/guardian.html` para el representante.

## 4. Parte 2: ejercicio de derechos

### 4.1 Flujo y estados

```
recibida → verificada → en_evaluacion → ejecutada → cerrada
                    ↘               ↘ rechazada  ↗
                     rechazada (identidad no verificada)
```

```sql
CREATE TABLE rights_requests (
  id TEXT PRIMARY KEY,                -- UUID
  subject TEXT NOT NULL,              -- seudónimo del titular
  type TEXT NOT NULL CHECK (type IN ('acceso', 'rectificacion', 'supresion')),
  status TEXT NOT NULL CHECK (status IN
    ('recibida', 'verificada', 'en_evaluacion', 'ejecutada', 'rechazada', 'cerrada')),
  received_at TEXT NOT NULL,
  verified_at TEXT,
  decided_at TEXT,
  closed_at TEXT,
  due_on TEXT NOT NULL,               -- received_at + 30 días corridos (art. 11)
  extended INTEGER NOT NULL DEFAULT 0, -- la prórroga se puede usar una sola vez
  reason TEXT                         -- código de motivo, nunca texto libre ni datos personales
);
```

- **Recepción:** `POST /me/requests` con `type`, la contraseña actual y, si es rectificación, los cambios.
- **Verificación:** se comprueba la contraseña (ver 4.2). Si falla, la solicitud pasa a `rechazada` con motivo
  `identidad_no_verificada` y el intento cuenta para el bloqueo de la cuenta.
- **Evaluación:** se aplican las reglas de negocio de cada tipo. Los casos que necesitan criterio humano quedan en
  `en_evaluacion`.
- **Ejecución o respuesta fundada:** `ejecutada`, o `rechazada` con un motivo de una lista cerrada
  (`ultimo_admin`, `conservacion_por_incidente`, `dato_invalido`, `alias_no_disponible`, `identidad_no_verificada`).
- **Cierre:** al responder, la solicitud pasa a `cerrada` con su fecha.

Los casos automáticos recorren todos los estados en una sola petición, cada transición con su fecha y su evento de
auditoría. Los casos `en_evaluacion` los resuelve un admin con `POST /admin/requests/{id}/decision`
(`ejecutar`, `rechazar` con motivo, o `prorrogar`, que suma 30 días una sola vez). `GET /admin/requests` lista las
solicitudes abiertas y marca las vencidas.

Los datos de una rectificación que queda en evaluación (por ejemplo, un nuevo alias) **no se guardan**: el admin
decide sobre el tipo de cambio y, si lo aprueba, la persona lo vuelve a enviar. Así la tabla de solicitudes no
acumula datos personales.

### 4.2 Verificación de identidad proporcional

| Operación | Verificación | Por qué |
|---|---|---|
| Ver sus consentimientos y solicitudes | Sesión válida | Solo lectura de metadatos propios |
| Acceso y portabilidad | Sesión **y** contraseña actual | Entrega todos los datos de la cuenta en un archivo |
| Rectificación | Sesión **y** contraseña actual | Cambia el identificador de inicio de sesión o el correo |
| Supresión | Sesión **y** contraseña actual | Irreversible |

No se piden datos nuevos (documento, preguntas de seguridad). El sistema conoce solo alias, contraseña y correo
opcional, y la reautenticación con la contraseña es la prueba más fuerte que tiene sin recolectar más.

Un error de contraseña cuenta para el bloqueo de 5 intentos del Hito 2, y además dispara la regla de alerta de la
sección 6. Una cuenta de menor de 14 ejerce sus derechos igual que cualquier otra; el representante sin cuenta lo
hace por el canal de contacto, de forma manual.

### 4.3 Acceso y portabilidad

La respuesta es un archivo JSON, legible por personas y por programas, con versión de formato:

```json
{
  "formato": "gamematch-export/1",
  "generado": "2026-11-12T15:04:05+00:00",
  "cuenta": {"alias": "jugador_1", "rol": "usuario", "creada": "2026-11-10",
             "correo": "persona@example.com", "menor_de_14": false},
  "consentimientos": [{"finalidad": "votos_analitica", "version": "1", "estado": "otorgado",
                       "actor": "titular", "fecha": "2026-11-10T13:00:00+00:00"}],
  "votos": [{"juego": "Hades", "voto": 1, "fecha": "2026-11-10"}],
  "solicitudes": [{"tipo": "acceso", "estado": "cerrada", "recibida": "2026-11-12T15:04:05+00:00"}]
}
```

**Excluido a propósito:** `password_hash`, `account.id`, seudónimos, contadores de bloqueo, la auditoría (registro
interno de seguridad que además contiene seudónimos de administradores, que son terceros) y las alertas. Se incluye el
correo descifrado: es dato del titular. La exportación queda en la auditoría como `data:export`.

### 4.4 Rectificación

| Campo | Validación | Efecto |
|---|---|---|
| `alias` | El patrón del Hito 2 (sin `@`) y único | Se actualiza. No hay que propagarlo: votos, consentimientos y auditoría usan el seudónimo del `id`, no el alias |
| `email` | Formato de correo y `correo_recuperacion` vigente | Se cifra y reemplaza. El envío de novedades lee el correo en cada envío, así que usa el nuevo |
| `is_child` | Ninguna automática | Queda `en_evaluacion`. Pasar de menor a mayor saltaría el control del representante |

El modelo de entrada usa `extra="forbid"`: no se puede rectificar el rol, el `id` ni otro campo no previsto. La
auditoría registra qué campos cambiaron (`fields=alias`), nunca los valores anterior ni nuevo. El cambio de contraseña
sigue en `PATCH /me`, que deja de aceptar `email`: ese cambio pasa por la rectificación.

### 4.5 Supresión

**Se ejecuta** cuando la contraseña es correcta y no aplica ninguna excepción. En una sola transacción:

1. Se borran los votos del seudónimo (`DELETE FROM feedback WHERE pseudonym = ?`).
2. Se registra un evento `revocado` con `actor = 'sistema'` por cada finalidad otorgada.
3. Se borra la fila de `accounts`, y con ella el correo cifrado y cualquier verificación pendiente.
4. Se audita `data:delete`.

**Cambio respecto del Hito 2.** Antes los votos quedaban sin vínculo y seguían sumando. Ahora se borran, porque la
supresión retira también la base para usarlos en reportes.

**Excepciones (fundamento para conservar):**

| Situación | Resultado | Fundamento |
|---|---|---|
| Es el último administrador activo | `rechazada`, motivo `ultimo_admin` | Integridad del servicio |
| La cuenta tiene `legal_hold = 1` por un incidente en investigación | `en_evaluacion`, motivo `conservacion_por_incidente` | Conservar evidencia mientras dura la investigación. El admin la ejecuta al levantar la retención |

**Lo que se conserva tras la supresión, y por qué:**

| Registro | Se conserva | Motivo | Plazo |
|---|---|---|---|
| `audit_log` | Sí, seudonimizado | Deber de seguridad: reconstruir incidentes | 365 días |
| `consent_events` | Sí, seudonimizado | Probar que los tratamientos pasados tuvieron base | 365 días |
| `rights_requests` | Sí, sin datos | Probar que la solicitud se atendió en plazo | 365 días desde el cierre |

Tras borrar la cuenta, su `id` ya no existe y el seudónimo no se puede recalcular desde el sistema.

**Copias y sistemas relacionados.** El proyecto no tiene respaldos automáticos. Si se restaura una copia de la base,
`python -m app.cli reapply-deletions` vuelve a aplicar las supresiones: recalcula el seudónimo de cada cuenta restaurada
y borra las que coinciden con una supresión ejecutada después de la fecha de la copia. Los artefactos de CI y la imagen
Docker no contienen datos personales (Hito 2), así que no hay nada que limpiar en ellos.

`DELETE /me` se reemplaza por `POST /me/requests` con `type = "supresion"`, que exige la contraseña.

### 4.6 Permisos nuevos

| Permiso | usuario | analista | admin |
|---|---|---|---|
| `consent:manage` | Sí | Sí | Sí |
| `rights:request` | Sí | Sí | Sí |
| `requests:read`, `requests:manage` | No | No | Sí |
| `alerts:read`, `alerts:manage` | No | No | Sí |

## 5. Parte 3: registro de auditoría

### 5.1 Eventos registrados

| Categoría | Acciones |
|---|---|
| Autenticación | `auth:register`, `auth:login` (permitido y denegado), `auth:guardian_confirm` |
| Consentimiento | `consent:grant`, `consent:revoke` |
| Derechos | `request:<tipo>:<estado>` en cada transición, `data:export`, `data:rectify`, `data:delete` |
| Cuenta propia | `me:update` |
| Administración | `accounts:read`, `accounts:manage`, `accounts:reveal_email`, `requests:decision`, `admin:legal_hold` |
| Seguridad | Toda denegación por permisos, `alert:<regla>`, `alert:update`, `audit:read`, `audit:verify`, `audit:purge` |
| Contención | `admin:revoke_sessions`, `system:read_only` |

### 5.2 Atributos

Se extiende `audit_log` sin perder las filas existentes:

| Columna | Contenido |
|---|---|
| `id`, `ts` | Existentes. `ts` en UTC con segundos |
| `event_id` | UUID, para citar un evento en un informe de incidente |
| `actor_pseudonym`, `target_pseudonym` | Existentes. HMAC de los ids |
| `action`, `outcome` | Existentes |
| `resource` | Recurso afectado por referencia: `consent:votos_analitica`, `request:<uuid>`, `account` |
| `detail` | Código sin datos personales: `fields=alias,email`, `version=1`, `motivo=ultimo_admin` |
| `prev_hash`, `hash` | Cadena de integridad (5.4) |

### 5.3 Lo que no se registra

Contraseñas (ni con error), tokens JWT, tokens del representante, claves, correos, alias, el valor anterior o nuevo de
una rectificación, el contenido de una exportación, cuerpos de peticiones, direcciones IP y `account.id` sin
seudonimizar. El filtro de logging del Hito 2 sigue como segunda barrera en los logs de la aplicación.

**Justificación.** Para investigar un incidente basta saber qué seudónimo hizo qué acción, sobre qué recurso, cuándo y
con qué resultado. Quien investiga con autorización puede recalcular el HMAC de una cuenta concreta y buscarla; quien
solo lee la auditoría no sabe quién es quién. Registrar valores convertiría la auditoría en una segunda copia de los
datos personales, con el mismo riesgo de filtración.

### 5.4 Protección

- **Sin modificación:** el trigger del Hito 2 bloquea todo `UPDATE`.
- **Sin borrado indebido:** el trigger de borrado se reemplaza por uno que solo deja borrar filas de más de 365 días.
- **Detección de alteraciones:** cada fila guarda `hash = HMAC-SHA256(GM_AUDIT_KEY, prev_hash || campos)`. Quien
  edite el archivo SQLite directamente, saltándose los triggers, no puede recalcular la cadena sin la clave.
  `python -m app.cli verify-audit` recorre la cadena y genera la alerta `integridad_auditoria` si se rompe.
  `GM_AUDIT_KEY` es una clave nueva, distinta de las otras tres.
- **Escritura serializada:** la inserción ocurre dentro de `BEGIN IMMEDIATE`, para que dos eventos simultáneos no
  usen el mismo `prev_hash`.
- **Filas anteriores al Hito 3:** quedan sin hash. La cadena parte en la primera fila con hash, y el informe lo dice.

### 5.5 Acceso

Solo `admin`, con `audit:read`. Cada lectura queda registrada como `audit:read`. El analista y el usuario no la ven.
Ningún rol puede modificarla por API, y no existe un endpoint de borrado.

### 5.6 Conservación y eliminación

`python -m app.cli purge-retention` (pensado para correr una vez al día) borra:

- Filas de `audit_log`, `consent_events` y `security_alerts` de más de 365 días. En `consent_events` no borra el
  último evento de cuentas que siguen existiendo, porque es su estado vigente.
- `rights_requests` cerradas hace más de 365 días.
- Cuentas de menores pendientes de representante con el token vencido.

Antes de purgar la auditoría registra `audit:purge` con la cantidad y el último hash eliminado, que pasa a ser el
ancla de la verificación. 365 días es una decisión del equipo: cubre el ciclo de un año de investigación y reclamos, y
se puede revisar.

## 6. Detección y alertas

Las reglas se evalúan al registrar el evento que las dispara, en el mismo proceso.

| Regla | Condición | Severidad |
|---|---|---|
| `login_fallido_reiterado` | 5 inicios de sesión fallidos para una cuenta en 15 minutos | media |
| `posible_fuerza_bruta` | 20 inicios fallidos en total en 5 minutos | alta |
| `exportacion_anomala` | Más de 3 exportaciones del mismo titular en 24 horas | media |
| `acceso_denegado_reiterado` | 10 denegaciones del mismo actor en 10 minutos | media |
| `revelacion_masiva` | Más de 5 `reveal-email` del mismo admin en 1 hora | alta |
| `integridad_auditoria` | `verify-audit` encuentra la cadena rota | crítica |

```sql
CREATE TABLE security_alerts (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  rule TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('media', 'alta', 'critica')),
  subject TEXT,                       -- seudónimo afectado, si aplica
  status TEXT NOT NULL DEFAULT 'abierta'
    CHECK (status IN ('abierta', 'en_analisis', 'descartada', 'incidente'))
);
```

Una regla no vuelve a abrir una alerta igual para el mismo seudónimo mientras haya otra abierta, para no inundar la
tabla. Cada alerta escribe una línea de log de nivel `WARNING` con la regla y la severidad, sin datos personales.
`GET /admin/alerts` y `PATCH /admin/alerts/{id}` (cambia el estado) exigen `alerts:read` y `alerts:manage`.

Los estados implementan la distinción del plan: `abierta` es una **alerta**; `en_analisis` es un **evento
sospechoso**; `incidente` es un **incidente confirmado**, que activa el plan.

## 7. Parte 4: plan de respuesta a incidentes

Va como documento propio (`docs/hito3/04-plan-incidentes.md`). Este es su contenido mínimo.

**Responsables (propuesta, a confirmar por el equipo):**

| Rol | Persona | Responsabilidad |
|---|---|---|
| Coordinación del incidente y seguridad | Nicolás Bravo | Declara el incidente, decide escalamientos, lleva la bitácora |
| Desarrollo | Maximiliano Mella | Corrige la causa, revisa el código, prepara el parche |
| Administración del sistema | Martín Lorca | Ejecuta contención y recuperación, preserva evidencia |
| Comunicación | Nicolás Bravo, con aprobación de los tres | Redacta y envía las notificaciones |

**Canales:** canal privado `#incidentes` en Discord para el equipo; reporte privado de vulnerabilidades de GitHub
(Security Advisories) para terceros; correo de contacto del equipo publicado en la página para los titulares.

**Severidad y escalamiento:**

| Nivel | Ejemplo | Escala a |
|---|---|---|
| SEV3 | Alerta media descartada tras análisis | Responsable de turno; se documenta |
| SEV2 | Cuenta comprometida sin acceso a datos de otros | Coordinación |
| SEV1 | Acceso a datos de varias cuentas, auditoría alterada, clave filtrada | Todo el equipo y evaluación de notificación |

**Contención** (con los comandos de la sección 8): revocar sesiones, suspender cuentas, activar solo lectura, rotar
claves. **Antes de cualquier cambio** se ejecuta `export-evidence`, que copia la base y su SHA-256, y se marca
`legal_hold` en las cuentas involucradas. Así la contención no destruye evidencia.

**Recuperación:** se corrige la causa con un PR revisado y CI en verde; antes de reabrir se verifica la cadena de
auditoría, que no queden sesiones antiguas válidas, que las claves comprometidas estén rotadas y que el smoke test pase.

**Notificación:**

- **Criterio:** se notifica a la Agencia si la vulneración genera un riesgo razonable para los derechos de los titulares
  (art. 14 sexies). Se notifica además a cada titular afectado si hay cuentas de menores de 14 involucradas.
- **Quién decide:** la coordinación propone y los tres integrantes aprueban.
- **Plazo:** la ley exige hacerlo "sin dilaciones indebidas" y no fija horas. El equipo se impone como meta interna
  decidir dentro de 24 horas desde la confirmación y notificar dentro de 72 horas. Es una meta del equipo, no un plazo
  legal, y el plan lo dice así.
- **Contenido:** los siete puntos que pide la actividad (descripción, fechas, datos afectados, consecuencias, medidas,
  recomendaciones, contacto), con una plantilla.

**Cierre:** el caso se cierra cuando la causa está corregida, la recuperación verificada, las notificaciones hechas o
descartadas con fundamento escrito, y existe un informe de lecciones aprendidas con causas, medidas y mejoras de
detección.

## 8. Parte 5: implementación

### 8.1 Módulos

| Módulo | Cambio |
|---|---|
| `db.py` | Tablas y triggers nuevos; `migrate()` agrega columnas a una base del Hito 2 sin perder datos |
| `consent.py` (nuevo) | Finalidades, `has_consent`, otorgar, revocar, historial y endpoints |
| `rights.py` (nuevo) | Solicitudes, máquina de estados, exportación, rectificación, supresión y endpoints |
| `guardian.py` (nuevo) | Tokens del representante y confirmación |
| `alerts.py` (nuevo) | Reglas, tabla de alertas y endpoints |
| `mailer.py` (nuevo) | Interfaz de envío. Implementación de prueba que escribe en `GM_OUTBOX_DIR` |
| `audit.py` | Columnas nuevas, cadena HMAC, `verify_chain()` y llamada a las reglas de alerta |
| `auth.py` | Registro con edad y consentimientos, `auth:login` auditado, `tokens_not_before`, bloqueo de cuentas pendientes |
| `me.py` | `PATCH /me` solo cambia la contraseña; se elimina `DELETE /me` |
| `reports.py` | Filtro por `votos_analitica` |
| `admin.py` | Solicitudes, alertas, `legal_hold`, bloqueo de activación de cuentas pendientes |
| `main.py` | Middleware de solo lectura; registro de routers |
| `cli.py` | Comandos de la sección 8.2 |
| `static/` | Registro, panel "Mi privacidad", `guardian.html` |

### 8.2 Procedimientos técnicos de contención y recuperación

| Comando `python -m app.cli` | Acción |
|---|---|
| `export-evidence --out DIR` | Copia la base con la API de respaldo de SQLite y escribe su SHA-256 |
| `revoke-sessions --account ID` o `--all` | Fija `tokens_not_before`; los JWT emitidos antes dejan de valer |
| `disable-account ID` | Suspende la cuenta |
| `legal-hold ID --on/--off` | Activa o levanta la retención por incidente |
| `read-only --on/--off` | Las peticiones que escriben responden 503, salvo `/admin` y el login |
| `verify-audit` | Verifica la cadena |
| `purge-retention` | Aplica la sección 5.6 |
| `reapply-deletions --since FECHA` | Vuelve a aplicar supresiones tras restaurar una copia |
| `newsletter` | Envío simulado de novedades, solo a quienes tienen `novedades_correo` |
| `demo-evidence` | Crea cuentas sintéticas en una base temporal, recorre todos los flujos e imprime los eventos de auditoría generados (evidencia) |

Cada comando queda en la auditoría. `tokens_not_before` necesita una columna en `accounts` y una tabla
`settings(key, value)` para el valor global y el modo solo lectura.

### 8.3 Por qué los controles no se pueden eludir

- **Una prueba recorre todas las rutas de la app** y falla si una ruta no pública no tiene la dependencia
  `require_permission`. Una ruta nueva sin permiso rompe el CI.
- **Las reglas están en la capa de datos:** el filtro de consentimiento está en la consulta SQL del reporte y de
  novedades, no en la página.
- **Triggers** bloquean la reescritura de `audit_log` y `consent_events`, también desde una consola SQLite.
- **La cadena HMAC** detecta lo que los triggers no impiden: alguien con acceso al archivo que borre los triggers.
- **La base no tiene interfaz de red:** SQLite es un archivo en un volumen del contenedor, que corre sin root. El
  único acceso es la API.
- **Las pruebas atacan la API directamente** con `TestClient`, sin pasar por la página: token de otro rol, token
  emitido antes de una revocación, cuenta pendiente, campos extra en el cuerpo, consentimiento de versión antigua.

### 8.4 Claves y configuración

Variables nuevas: `GM_AUDIT_KEY` (obligatoria para todo lo que audita; sin ella responde 503, como las demás) y
`GM_OUTBOX_DIR` (buzón simulado). Las pruebas generan ambas en cada corrida.

## 9. Pruebas

| Archivo | Comprueba |
|---|---|
| `test_consentimiento.py` | Registro por finalidad con fecha, versión y estado; historial; revocación con fecha; versión nueva invalida la anterior; una finalidad no habilita otra (novedades con correo de recuperación); reporte excluye a quien revoca; correo se borra al revocar; triggers de solo inserción |
| `test_menores.py` | Cuenta pendiente sin login; correo del representante no se guarda; token de un solo uso y con vencimiento; admin no puede activar una pendiente; menor no otorga pero sí revoca; `novedades_correo` no disponible |
| `test_derechos.py` | Estados y fechas; contraseña incorrecta rechaza y cuenta para el bloqueo; exportación sin hash, id ni seudónimos; rectificación validada y sin escalar rol; `is_child` va a evaluación; supresión borra votos y revoca consentimientos; último admin; `legal_hold`; prórroga única; `reapply-deletions` |
| `test_auditoria.py` | Atributos de cada evento; sin datos personales en ninguna fila tras un recorrido completo; cadena válida; alteración detectada; borrado antes de 365 días bloqueado; lectura auditada |
| `test_alertas.py` | Cada regla se dispara en su umbral y no antes; sin duplicados abiertos; cambio de estado auditado |
| `test_contencion.py` | Revocar sesiones invalida tokens anteriores; solo lectura bloquea escrituras; `export-evidence` produce una copia con hash correcto |
| `test_rutas.py` | Inventario de rutas: toda ruta no pública exige permiso |
| Pruebas existentes | Se actualizan `DELETE /me` y `PATCH /me` con correo al nuevo contrato, y la matriz de `test_rbac.py` con las rutas nuevas |

La evidencia son los resultados de `pytest` en CI, ejemplos reales de filas de auditoría generadas por una sesión de
prueba (`python -m app.cli demo-evidence` crea cuentas sintéticas, recorre los flujos e imprime los eventos) y capturas
de las pantallas.

## 10. Entrega por etapas

No se tiene permiso de escritura sobre el repositorio desde esta sesión. Las ramas se preparan en local y se entregan
como un paquete Git (`git bundle`) para que el equipo las publique y abra los PR con la plantilla y la revisión cruzada.

| PR | Rama | Contenido |
|---|---|---|
| 1 | `docs/diseno-hito3` | Este documento |
| 2 | `feature/consentimiento` | `consent.py`, `guardian.py`, `mailer.py`, migración, registro, reportes filtrados, pantallas |
| 3 | `feature/derechos` | `rights.py`, cambios en `me.py` y `admin.py`, panel "Mis datos" |
| 4 | `feature/auditoria-alertas` | Auditoría extendida, `alerts.py`, comandos de contención, inventario de rutas |
| 5 | `docs/informe-hito3` | Los cuatro documentos de diseño, el plan de incidentes, evidencias y justificación técnica |

## 11. Limitaciones y riesgos que quedan

- **Verificación del representante por correo.** Prueba control del correo, no la relación legal ni la edad real.
- **Correo simulado.** No hay servidor de correo; el buzón local demuestra la lógica. En producción el enlace viajaría
  por SMTP y el correo del representante no quedaría escrito en disco.
- **Edad declarada.** Un menor puede responder que no lo es. Se acepta por la baja sensibilidad de los datos.
- **SQLite.** Quien tenga escritura sobre el archivo puede borrar triggers; la cadena HMAC lo detecta, pero no lo
  impide. Postgres con roles y `GRANT` lo resolvería.
- **Alertas en el mismo proceso.** Si la API cae, no hay detección. Un sistema externo (SIEM) quedaría para producción.
- **Sin respaldos automáticos.** El procedimiento `reapply-deletions` está listo, pero hoy no hay copias que restaurar.
- **Plazos internos de notificación.** La meta de 72 horas es del equipo; la ley exige "sin dilaciones indebidas" y la
  Agencia todavía no ha dictado instrucciones sobre cómo medirlo.
