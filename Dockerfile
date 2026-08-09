# FreightPrint application image.
#
# The route cache lives on a volume rather than in the image: a cold container would
# otherwise replay every OSRM call it has ever made, and one route request costs seven.

FROM python:3.13-slim AS base

# Shapely and searoute ship wheels with their own GEOS, so no build toolchain is needed.
# curl is here for the healthcheck only.
RUN apt-get update \
 && apt-get install --no-install-recommends -y curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements are copied on their own so a source edit does not reinstall the world.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY data/ ./data/
COPY scripts/ ./scripts/

# The engine only reads reference data and writes its cache, so it does not need root.
# The cache goes in /app/var, deliberately not under /app/data: that directory holds the
# reference data shipped with the code, and a volume mounted over it to keep a cache
# would pin an old factor table across every upgrade.
RUN useradd --create-home --uid 10001 freight \
 && mkdir -p /app/var \
 && chown -R freight:freight /app
USER freight

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FREIGHTPRINT_CACHE_DIR=/app/var \
    PORT=8000

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# One worker by default. Routing is I/O bound and already runs in a threadpool, and the
# OSRM concurrency gate is per process — more workers would multiply it silently.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
