# MIADP application image (used for both the API and the Streamlit dashboard).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app

# GeoPandas/pyproj/shapely ship manylinux wheels; only curl is needed for health checks.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

COPY alembic.ini tasks.py ./
COPY alembic ./alembic
COPY api ./api
COPY app ./app
COPY config ./config
COPY assets ./assets
COPY scripts ./scripts
COPY .streamlit ./.streamlit

RUN useradd --create-home --uid 10001 miadp && mkdir -p /app/data /app/models /app/reports \
    && chown -R miadp:miadp /app
USER miadp

EXPOSE 8000 8501
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
