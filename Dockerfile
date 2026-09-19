FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY fcapsule ./fcapsule
RUN python -m pip install --no-cache-dir . \
    && addgroup --system fcapsule \
    && adduser --system --ingroup fcapsule --home /var/lib/fcapsule fcapsule \
    && mkdir -p /var/lib/fcapsule \
    && chown -R fcapsule:fcapsule /var/lib/fcapsule

USER fcapsule
EXPOSE 8765

ENTRYPOINT ["python", "-m", "fcapsule.cli", "serve"]
CMD ["--host", "0.0.0.0", "--port", "8765", "--state-dir", "/var/lib/fcapsule"]
