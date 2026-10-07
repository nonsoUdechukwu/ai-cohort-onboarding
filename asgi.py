"""ASGI entry point: `gunicorn -k uvicorn.workers.UvicornWorker asgi:app` or `uvicorn asgi:app`."""
from portal import create_app

app = create_app()
