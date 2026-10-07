"""gunicorn settings for Azure App Service F1 (FastAPI via uvicorn workers).

One async worker process by default: it fits F1's 1 GB memory comfortably and keeps the
in-memory rate-limit counters consistent. ``WEB_CONCURRENCY=2`` is possible on F1, but each
worker then has its own counters (effective per-IP limit doubles).
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
timeout = 60
accesslog = "-"
errorlog = "-"
# App Service's front end is the only peer; uvicorn may trust its X-Forwarded-* headers.
forwarded_allow_ips = "*"
