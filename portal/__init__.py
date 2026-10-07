"""AI Cohort Onboarding Portal - FastAPI application factory."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from .net import client_ip  # noqa: F401  (re-exported)
from .ratelimit import RateLimiter

BASE_DIR = Path(__file__).resolve().parent
MAX_CONTENT_LENGTH = 16 * 1024

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://challenges.cloudflare.com; "
    "frame-src https://challenges.cloudflare.com; "
    "connect-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def create_app(
    settings: Optional[Settings] = None,
    *,
    graph: Any = None,
    store: Any = None,
    turnstile: Any = None,
) -> FastAPI:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = settings or Settings.from_env()
    owned = []  # clients created here, closed on shutdown

    if store is None:
        from .storage import create_store

        store = create_store(settings.storage_connection_string, settings.table_name)
    if turnstile is None:
        from .turnstile import TurnstileVerifier

        turnstile = TurnstileVerifier(settings.turnstile_secret_key)
        owned.append(turnstile)
    if graph is None:
        from .graph import GraphClient, build_credential

        graph = GraphClient(
            build_credential(
                settings.tenant_id,
                settings.client_id,
                settings.client_secret,
                settings.managed_identity_client_id,
            )
        )
        owned.append(graph)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        for client in owned:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - best effort on shutdown
                logging.getLogger(__name__).warning("Error closing %s", type(client).__name__)

    # API docs are disabled: the app has no public API surface beyond the form.
    app = FastAPI(
        title="AI Cohort Onboarding Portal",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.portal = {
        "settings": settings,
        "graph": graph,
        "store": store,
        "turnstile": turnstile,
        # In-memory is fine: F1 runs a single instance and gunicorn uses one worker process.
        "limiter": RateLimiter(settings.register_rate_limit),
    }
    app.state.templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

    from .routes import router

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_CONTENT_LENGTH)
    app.add_middleware(SecurityHeadersMiddleware, csp=CSP)

    missing = settings.missing_required()
    if missing:
        logging.getLogger(__name__).warning("Missing configuration: %s", ", ".join(missing))
    return app
