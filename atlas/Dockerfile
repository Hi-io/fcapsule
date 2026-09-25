FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /srv
COPY atlas/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt \
    && useradd --system --uid 10001 --create-home atlas
COPY atlas /srv/atlas
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8080')+'/healthz',timeout=2)"
CMD ["sh", "-c", "uvicorn atlas.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
