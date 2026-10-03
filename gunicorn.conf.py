"""gunicorn settings for Azure App Service F1.

One worker process keeps Flask-Limiter's in-memory counters consistent; threads handle
concurrency while requests wait on Microsoft Graph.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 60
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = "*"
