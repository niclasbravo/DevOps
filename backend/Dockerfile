# Imagen de la API de GameMatch
FROM python:3.12-slim

# Python sin archivos .pyc y con logs inmediatos
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Dependencias primero: esta capa se reutiliza mientras requirements.txt no cambie
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Código de la aplicación
COPY app/ ./app/

# 3) No correr como root dentro del contenedor
RUN useradd --create-home --uid 10001 gamematch
USER gamematch

EXPOSE 8000

# Docker marca el contenedor como "unhealthy" si /health deja de responder
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
