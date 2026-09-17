# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    SHOWCASE_BIND_HOST=0.0.0.0 \
    SHOWCASE_PORT=18080 \
    SHOWCASE_PUBLIC_PORT=18080 \
    SHOWCASE_FIXED_PORTS=1 \
    SHOWCASE_DOCKER=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    MPLCONFIGDIR=/tmp/matplotlib \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libgomp1 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 showcase \
    && useradd --uid 10001 --gid showcase --create-home showcase

WORKDIR /app
COPY requirements-lock.txt ./requirements-lock.txt
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements-lock.txt && pip check

COPY --chown=showcase:showcase . /app
RUN python docker/prepare_image.py \
    && mkdir -p /data \
    && chown -R showcase:showcase /data /opt/showcase-seeds /app

USER showcase
VOLUME ["/data"]
EXPOSE 18080 18081 18082 18083 18084 18085 18086 18087 18088
HEALTHCHECK --interval=20s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "/app/docker/healthcheck.py"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "/app/docker/entrypoint.py"]
