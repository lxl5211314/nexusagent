# Slim runtime image: same code as local dev, no external services required.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEXUS_DATA_DIR=/data

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data/uploads

EXPOSE 8017

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8017/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8017"]
