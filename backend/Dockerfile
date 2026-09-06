# ResearchFlow AI — backend image.
#
# Layout matters: config.py derives PROJECT_ROOT from its own location
# (parents[3] of app/core/config.py). With the package at /app/backend/app,
# PROJECT_ROOT resolves to /app — so data/, reports/ and .models/ all land
# under /app, where the docker-compose volumes mount the persistent host
# directories. Do NOT flatten the package to /app/app (that would move the
# database and Chroma store to the read-only filesystem root).

FROM python:3.14-slim

# Never buffer stdout/stderr (uvicorn logs must reach docker logs immediately)
# and never write .pyc files into the image layers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend

# Install dependencies first for better layer caching. torch comes from the
# CPU-only wheel index (the default PyPI wheel bundles CUDA, ~2 GB larger
# and useless in this container). Installing torch separately up front lets
# the requirements install see it already satisfied.
COPY requirements.txt .
RUN pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# Application code only — tests, .venv, .env and caches are excluded by
# .dockerignore (secrets enter at RUNTIME via env_file, never the image).
COPY app ./app

# Run as an unprivileged user (uid 1000 matches the default host user that
# owns the mounted ./data and ./reports directories on Linux).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data /app/reports \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
