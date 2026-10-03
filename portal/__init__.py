"""AI Cohort Onboarding Portal - Flask application factory."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Settings

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://challenges.cloudflare.com; "
    "frame-src https://challenges.cloudflare.com; "
    "connect-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def client_ip() -> str:
    """Client IP after ProxyFix. App Service may append ':port' to X-Forwarded-For entries."""
    addr = (request.remote_addr or "").strip()
    if addr.startswith("[") and "]" in addr:  # [ipv6]:port
        return addr[1 : addr.index("]")]
    if addr.count(":") == 1:  # ipv4:port
        return addr.split(":", 1)[0]
    return addr or "unknown"


def create_app(
    settings: Optional[Settings] = None,
    *,
    graph: Any = None,
    store: Any = None,
    turnstile: Any = None,
) -> Flask:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = settings or Settings.from_env()

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

    if store is None:
        from .storage import create_store

        store = create_store(settings.storage_connection_string, settings.table_name)
    if turnstile is None:
        from .turnstile import TurnstileVerifier

        turnstile = TurnstileVerifier(settings.turnstile_secret_key)
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

    # In-memory storage is fine: F1 runs a single instance and gunicorn uses one worker process.
    limiter = Limiter(key_func=client_ip, app=app, storage_uri="memory://", default_limits=[])

    app.extensions["portal"] = {
        "settings": settings,
        "graph": graph,
        "store": store,
        "turnstile": turnstile,
        "limiter": limiter,
    }

    from .routes import api_bp, main_bp

    limiter.limit(settings.register_rate_limit, methods=["POST"])(api_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    @app.errorhandler(429)
    def too_many_requests(_e):
        return (
            jsonify(ok=False, error="Too many attempts. Please wait a few minutes and try again."),
            429,
        )

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if request.path.startswith(("/admin", "/api/")):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    missing = settings.missing_required()
    if missing:
        logging.getLogger(__name__).warning("Missing configuration: %s", ", ".join(missing))
    return app
