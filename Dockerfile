FROM python:3.12-slim

# Don't write .pyc files; flush stdout/stderr immediately (so logs show in docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer caches when only source changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# data/ holds the community reports SQLite DB; static/maps holds per-session maps
RUN mkdir -p static/maps data
VOLUME ["/app/data"]

EXPOSE 5001

# IMPORTANT: a single worker is required — app._last_ctx is an in-memory singleton
# shared across requests. Multiple workers would not see each other's last search.
# --timeout 120 because OSRM route calls are slow and sequential.
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "1", "--timeout", "120", "app:app"]
