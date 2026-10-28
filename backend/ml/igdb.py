"""Cliente mínimo de la API de IGDB (v4).

Autenticación: token "client credentials" de Twitch con TWITCH_CLIENT_ID y
TWITCH_CLIENT_SECRET. Esas credenciales viven en backend/.env (local) o en los
secrets de GitHub Actions; nunca en el repositorio.
"""

import os
import time
from pathlib import Path

import requests

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_URL = "https://api.igdb.com/v4"
PAGE_SIZE = 500  # máximo que acepta IGDB por consulta
MIN_INTERVAL = 0.26  # IGDB permite 4 consultas por segundo


def load_dotenv(path: Path) -> None:
    """Carga variables KEY=VALUE de un .env sin pisar las que ya existen en el entorno."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class IGDBClient:
    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._token: str | None = None
        self._last_call = 0.0

    @classmethod
    def from_env(cls) -> "IGDBClient":
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        client_id = os.environ.get("TWITCH_CLIENT_ID")
        client_secret = os.environ.get("TWITCH_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError("Faltan TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET (backend/.env o secrets de Actions)")
        return cls(client_id, client_secret)

    def _authenticate(self) -> str:
        resp = self.session.post(
            TOKEN_URL,
            data={"client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def query(self, endpoint: str, body: str, retries: int = 5) -> list | dict:
        """Ejecuta una consulta Apicalypse respetando el límite de 4 req/s y reintentando ante 429 o 5xx."""
        token = self._token or self._authenticate()
        for attempt in range(retries):
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            resp = self.session.post(
                f"{API_URL}/{endpoint}",
                data=body.encode("utf-8"),
                headers={"Client-ID": self.client_id, "Authorization": f"Bearer {token}"},
                timeout=60,
            )
            if resp.status_code == 401 and attempt == 0:
                token = self._authenticate()
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"IGDB no respondió después de {retries} intentos ({endpoint})")
