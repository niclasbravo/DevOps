## Recorrido

| Paso | Petición | Respuesta |
|---|---|---|
| Registro con una finalidad marcada | `POST /auth/register` | 201 |
| Otorga una segunda finalidad | `POST /me/consents/correo_recuperacion` | 200 |
| Revoca votos_analitica | `DELETE /me/consents/votos_analitica` | 200 |
| Consulta el historial | `GET /me/consents/history` | 200 |
| Registro de menor de 14 (queda pendiente) | `POST /auth/register` | 201 |
| El menor intenta entrar antes de la confirmación | `POST /auth/login` | 401 |
| El representante confirma con el enlace | `POST /guardian/confirm` | 200 |
| Reusar el mismo enlace | `POST /guardian/confirm` | 400 |
| El menor entra después de la confirmación | `POST /auth/login` | 200 |
| Acceso y portabilidad (descarga JSON) | `POST /me/requests` | 200 |
| Acceso con contraseña incorrecta | `POST /me/requests` | 403 |
| Rectificación del alias | `POST /me/requests` | 200 |
| Intento de rectificar el rol | `POST /me/requests` | 422 |
| 5 inicios fallidos sobre una cuenta (dispara alerta) | `POST /auth/login` | 401 |
| Un usuario intenta ver las alertas | `GET /admin/alerts` | 403 |
| El admin ve las alertas | `GET /admin/alerts` | 200 |
| Token emitido antes de revocar todas las sesiones | `GET /me` | 401 |
| Escritura en modo solo lectura | `POST /me/consents/votos_analitica` | 503 |
| Lectura en modo solo lectura | `GET /me` | 200 |
| Supresión de la cuenta | `POST /me/requests` | 200 |
| La cuenta suprimida intenta entrar | `POST /auth/login` | 401 |

Cadena de auditoría íntegra: `(True, None)`. Copia de evidencia con SHA-256 `1e375cfa4d5445d8…`.

## Historial de consentimientos de demo_ana

| Fecha | Finalidad | Estado | Versión | Actor |
|---|---|---|---|---|
| 2026-11-15T01:45:17+00:00 | votos_analitica | otorgado | 1 | titular |
| 2026-11-15T01:45:17+00:00 | correo_recuperacion | otorgado | 1 | titular |
| 2026-11-15T01:45:17+00:00 | votos_analitica | revocado | 1 | titular |

## Exportación de acceso y portabilidad

```json
{
  "formato": "gamematch-export/1",
  "generado": "2026-11-15T01:45:17+00:00",
  "cuenta": {
    "alias": "demo_ana",
    "rol": "usuario",
    "creada": "2026-11-15",
    "correo": null,
    "menor_de_14": false
  },
  "consentimientos": [
    {
      "finalidad": "votos_analitica",
      "version": "1",
      "estado": "otorgado",
      "actor": "titular",
      "fecha": "2026-11-15T01:45:17+00:00"
    },
    {
      "finalidad": "correo_recuperacion",
      "version": "1",
      "estado": "otorgado",
      "actor": "titular",
      "fecha": "2026-11-15T01:45:17+00:00"
    },
    {
      "finalidad": "votos_analitica",
      "version": "1",
      "estado": "revocado",
      "actor": "titular",
      "fecha": "2026-11-15T01:45:17+00:00"
    }
  ],
  "votos": [],
  "solicitudes": [
    {
      "tipo": "acceso",
      "estado": "cerrada",
      "recibida": "2026-11-15T01:45:17+00:00"
    }
  ]
}
```

## Solicitudes de derechos registradas

| Tipo | Estado | Motivo | Recibida | Vence | Prorrogada |
|---|---|---|---|---|---|
| acceso | cerrada | - | 2026-11-15T01:45:17+00:00 | 2026-12-15 | no |
| acceso | cerrada | identidad_no_verificada | 2026-11-15T01:45:17+00:00 | 2026-12-15 | no |
| rectificacion | cerrada | - | 2026-11-15T01:45:17+00:00 | 2026-12-15 | no |
| supresion | cerrada | - | 2026-11-15T01:45:18+00:00 | 2026-12-15 | no |

## Alertas de seguridad generadas

| Fecha | Regla | Severidad | Estado |
|---|---|---|---|
| 2026-11-15T01:45:18+00:00 | login_fallido_reiterado | media | abierta |

## Eventos de auditoría generados (extracto)

Los actores y objetivos aparecen solo como seudónimos HMAC; `hash` encadena cada fila con la anterior.

| event_id | Fecha | Acción | Recurso | Actor | Resultado | Detalle | hash |
|---|---|---|---|---|---|---|---|
| 7266482f | 01:45:17 | auth:register | - | ac6171562f | permitido | - | 63e677a4ad |
| abb1a950 | 01:45:17 | auth:login | account | ac6171562f | permitido | - | 3f3d845233 |
| dd01c042 | 01:45:17 | consent:grant | consent:correo_recuperacion | ac6171562f | permitido | - | 9610c1c0dc |
| f6ef8dd3 | 01:45:17 | consent:revoke | consent:votos_analitica | ac6171562f | permitido | - | ab9bc7cb29 |
| dece4270 | 01:45:17 | auth:register | - | a877fca405 | permitido | - | 675d26d6ce |
| 7d203d52 | 01:45:17 | auth:login | account | - | denegado | - | c28c2f49f7 |
| 78729184 | 01:45:17 | auth:guardian_confirm | - | - | permitido | - | 1e87067e6e |
| 3b38068f | 01:45:17 | auth:guardian_confirm | - | - | denegado | - | c74e4380d8 |
| 7c546c79 | 01:45:17 | auth:login | account | a877fca405 | permitido | - | 17d74e274c |
| 670f7fa9 | 01:45:17 | data:export | - | ac6171562f | permitido | - | 6600c96a31 |
| 345f8d2a | 01:45:17 | rights:acceso | - | ac6171562f | denegado | - | 932f4162cd |
| 089d7b26 | 01:45:17 | data:rectify | - | ac6171562f | permitido | campos=alias | 3647da9b0a |
| 458d7130 | 01:45:17 | auth:login | account | - | denegado | - | a655a6e27a |
| efa2e3a3 | 01:45:17 | auth:login | account | - | denegado | - | 5e592758ab |
| 62c75777 | 01:45:18 | auth:login | account | - | denegado | - | 929f79f1e0 |
| b9dcf3b3 | 01:45:18 | auth:login | account | - | denegado | - | df41a69a3c |
| 57bfaf18 | 01:45:18 | auth:login | account | - | denegado | - | e7647344ec |
| a92bae5a | 01:45:18 | alert:login_fallido_reiterado | security_alerts | - | permitido | - | 756f885007 |
| 1b54e97f | 01:45:18 | auth:login | account | - | denegado | - | 0e453615b7 |
| 78681286 | 01:45:18 | auth:login | account | 93afcfc64d | permitido | - | 65748d7ab1 |
| 0d6c9e63 | 01:45:18 | alerts:read | - | ac6171562f | denegado | - | 2628f662e7 |
| 61f51e4c | 01:45:18 | auth:login | account | 7ae1dbac4a | permitido | - | c04696887e |
| 683ba524 | 01:45:18 | admin:revoke_sessions | account | - | permitido | todas | 352425bd4b |
| 721e3414 | 01:45:18 | me:read | - | - | denegado | - | b7f613de75 |
| a9aa888e | 01:45:18 | auth:login | account | 7ae1dbac4a | permitido | - | cd2855573a |
| 0a3ac5b8 | 01:45:18 | system:read_only | - | - | permitido | activado | bc76bd288e |
| 9dd46bdc | 01:45:18 | system:read_only | - | - | permitido | desactivado | ce6aabd546 |
| 75a90118 | 01:45:18 | data:delete | - | 7ae1dbac4a | permitido | - | 83c6683abf |
| b6f9f2f8 | 01:45:18 | auth:login | account | - | denegado | - | 35002237cc |
| 856babe4 | 01:45:18 | system:export_evidence | database | - | permitido | sha256=1e375cfa4d5445d8 | 72ab527b19 |

Total de eventos: 30. Se comprobó que ningún alias, correo ni contraseña del recorrido aparece en la auditoría.
