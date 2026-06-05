# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Gunicorn is the production WSGI server — Flask's built-in dev server
# is not suitable for production.
# Workers: 2-4 is appropriate for a low-traffic internal tool.
# Bind to 8000 to match Azure Container Apps expected port.
CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8000", "--timeout", "120", "app:app"]
