from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from core.database import connect_to_mongo, close_mongo_connection, get_database
from core.config import validate_config, config
from core.health_check import health_check_system
from core.scheduler import scheduler
from core.http_clients import close_http_clients
from core.db_indexes import ensure_indexes
from routers import auth, chatbot, chat, conversations, upload, widget, permissions, audit, pages, alerts, leads, analytics
from services.rag_service import warmup_models
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import os
import logging
import sys

logger = logging.getLogger(__name__)

# ==========================================
# 1. VALIDER LA CONFIGURATION AU DÉMARRAGE
# ==========================================
if not validate_config():
    logger.critical("❌ Configuration invalide. Arrêt du backend.")
    sys.exit(1)

ENV = config.get("ENV", "development")
docs_url = "/docs" if ENV == "development" else None
redoc_url = "/redoc" if ENV == "development" else None

app = FastAPI(
    title="Chatbot API",
    version="1.0.0",
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url="/openapi.json" if ENV == "development" else None,
)

# Rate-limit counters are stored in shared Redis so the limits hold across all
# worker processes (uvicorn workers / replicas); falls back to in-process memory
# only if REDIS_URL is unset.
_rate_limit_storage = os.getenv("REDIS_URL")
if _rate_limit_storage:
    limiter = Limiter(key_func=get_remote_address, storage_uri=_rate_limit_storage)
else:
    limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    logger.error(f"Validation error for {request.url}: {errors}")
    try:
        body = await request.body()
        logger.error(f"Request body: {body.decode('utf-8')}")
    except Exception as e:
        logger.warning(f"Could not read request body: {e}")

    formatted_errors = []
    for error in errors:
        formatted_errors.append({
            "type": error.get("type"),
            "loc": error.get("loc"),
            "msg": error.get("msg"),
            "input": error.get("input"),
        })

    return JSONResponse(status_code=422, content={"detail": formatted_errors})


# ──────────────────────────────────────────────────────────────────────────────
# CORS — split policy for the admin (credentialed) vs the widget (public)
# ──────────────────────────────────────────────────────────────────────────────
# The admin dashboard sends a JWT cookie + CSRF cookie, so it needs
# `Access-Control-Allow-Credentials: true` and a finite list of allowed origins.
# The widget is embedded on third-party customer sites we do not know in
# advance; it does NOT use cookies (it authenticates per chatbot via X-API-Key)
# so it answers with `Access-Control-Allow-Origin: <request origin>` and
# `Allow-Credentials: false`. The CORS spec forbids `*` together with
# credentials, so we route each request through the right policy by path.

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://localhost:3002,http://localhost:3003",
).split(",")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS if o.strip()]

PUBLIC_WIDGET_PREFIXES = (
    "/chat/",               # /chat/, /chat/stream  (trailing slash so /chatbot/ is NOT matched)
    "/chatbot/public/",     # widget bootstrap config
    "/widget.js",           # public CDN-style widget loader (no auth — third-party embed)
    "/leads/submit",        # public lead capture
    # NOTE: "/widget/" must NOT be public — /widget/build and /widget/download are
    # AUTHENTICATED admin actions (they need the JWT cookie). Treating them as
    # public stripped Access-Control-Allow-Credentials, so the admin's credentialed
    # download/build requests were blocked by the browser. The real public widget
    # bootstrap is "/chatbot/public/" (above), not "/widget/".
    "/health",              # /health, /health/detailed, /health/components/{name}
    "/metrics",
)


def _is_public_widget_path(path: str) -> bool:
    if path.startswith(PUBLIC_WIDGET_PREFIXES):
        return True
    # Public message-feedback action (widget thumbs up/down). Matched narrowly so
    # the credentialed /conversations/ admin endpoints (list, feedback-stats) keep
    # the strict CORS policy — only "/conversations/{id}/feedback" is public.
    if path.startswith("/conversations/") and path.rstrip("/").endswith("/feedback"):
        return True
    return False


class WidgetCORSMiddleware(BaseHTTPMiddleware):
    """Custom CORS handler for public widget endpoints.

    Echoes the request Origin back without credentials so any third-party site
    can embed the widget. Pre-flight (OPTIONS) requests are short-circuited.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_widget = _is_public_widget_path(path)
        origin = request.headers.get("origin", "")

        if is_widget and request.method == "OPTIONS":
            headers = {
                "Access-Control-Allow-Origin": origin or "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            }
            return Response(status_code=204, headers=headers)

        response = await call_next(request)

        if is_widget and origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "false"
            response.headers["Vary"] = "Origin"

        return response


# Restrictive CORS for the credentialed admin endpoints (everything that is
# NOT a widget path).
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-API-Key"],
)
# Widget CORS — added AFTER so it runs first (Starlette runs middleware in
# reverse-add order). It only writes headers when the path is widget-public.
app.add_middleware(WidgetCORSMiddleware)


# ──────────────────────────────────────────────────────────────────────────────
# Security headers middleware
# ──────────────────────────────────────────────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Server"] = "webserver"
    # CSP only for non-widget paths (the widget responses are JSON / SSE that
    # the host page consumes via fetch, no CSP needed).
    if not _is_public_widget_path(request.url.path):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; font-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'"
        )
    return response


os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


def _start_conversation_drain_thread():
    """Background thread that drains the Redis-buffered conversations into
    MongoDB. Runs forever; daemonised so it dies with the process."""
    import threading
    import time
    from pymongo import MongoClient

    def _loop():
        from services.conversation_buffer import drain_once
        log = logging.getLogger("conv-drain")
        try:
            db_name = os.getenv("DATABASE_NAME", "chatbot_db")
            client = MongoClient(
                os.getenv("MONGODB_URL", "mongodb://localhost:27017"),
                serverSelectionTimeoutMS=5000,
            )
            sync_db = client[db_name]
        except Exception as e:
            log.error(f"drain thread: failed to open Mongo client: {e}")
            return
        log.info("drain thread started")
        while True:
            try:
                drain_once(sync_db)  # blocks up to 2s on XREADGROUP, self-paces
            except Exception as e:
                log.error(f"drain loop iteration failed: {e}")
                time.sleep(2)

    t = threading.Thread(target=_loop, daemon=True, name="conversation-drain")
    t.start()


@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Démarrage du backend...")
    await connect_to_mongo()

    # Create / verify all MongoDB indexes used by hot-path queries.
    try:
        await ensure_indexes(get_database())
    except Exception as e:
        logger.error(f"Index creation failed: {e}")

    # Start the conversation buffer drain BEFORE we accept traffic so any
    # leftover entries from a previous run get flushed.
    try:
        _start_conversation_drain_thread()
    except Exception as e:
        logger.error(f"Failed to start conversation drain thread: {e}")

    import asyncio
    try:
        await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, warmup_models),
            timeout=120,
        )
        logger.info("✅ RAG models warmed up successfully")
    except asyncio.TimeoutError:
        logger.warning("⚠️  Model warmup timed out, will load on first use")
    except Exception as e:
        logger.error(f"❌ Model warmup failed: {e}")

    health_check_interval = config.get_int("HEALTH_CHECK_INTERVAL", 60)
    await health_check_system.start(interval=health_check_interval)
    await scheduler.start()
    logger.info("✨ Backend prêt !")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Arrêt du backend...")
    await scheduler.stop()
    await health_check_system.stop()
    await close_http_clients()
    await close_mongo_connection()
    logger.info("👋 Backend arrêté")


app.include_router(auth.router)
app.include_router(chatbot.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(upload.router)
app.include_router(widget.router)
app.include_router(permissions.router)
app.include_router(audit.router)
app.include_router(pages.router)
app.include_router(alerts.router)
app.include_router(leads.router)
app.include_router(analytics.router)


@app.get("/")
def read_root():
    return {
        "message": "Chatbot Backend API is running!",
        "version": "1.0.0",
        "environment": config.get("ENV"),
        "docs": "/docs" if config.is_development() else None,
    }


# Public CDN-style widget loader. Customers embed the bot with:
#   <script src="<API_URL>/widget.js" data-chatbot-id="<id>" defer></script>
# Same self-contained bundle as the authenticated /widget/download, but served
# with no auth so third-party sites can load it directly. The bundle reads the
# chatbot id from its own <script data-chatbot-id> tag and calls the public
# /chatbot/public/ + /chat/ endpoints. Access control stays on those endpoints
# (per-chatbot allowed_domains), not on this static asset.
_WIDGET_BUNDLE_FILE = (
    "/widget/build/widget.bundle.js"
    if os.path.exists("/widget/build/widget.bundle.js")
    else os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "widget", "build", "widget.bundle.js"))
)


@app.get("/widget.js")
async def serve_widget_bundle():
    if not os.path.exists(_WIDGET_BUNDLE_FILE):
        return JSONResponse(status_code=404, content={"detail": "Widget bundle not built yet."})
    return FileResponse(
        _WIDGET_BUNDLE_FILE,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/health")
async def health_check():
    from core.health_check import HealthStatus
    overall_status = health_check_system.get_overall_status()
    if overall_status == HealthStatus.UNHEALTHY:
        return JSONResponse(status_code=503, content={"status": "unhealthy"})
    elif overall_status == HealthStatus.DEGRADED:
        return JSONResponse(status_code=200, content={"status": "degraded"})
    return {"status": "healthy"}


@app.get("/health/detailed")
async def health_check_detailed():
    return health_check_system.get_health_report()


@app.get("/health/components/{component_name}")
async def health_check_component(component_name: str):
    if component_name not in health_check_system.components:
        return JSONResponse(status_code=404, content={"error": f"Component {component_name} not found"})
    return health_check_system.components[component_name].to_dict()


@app.get("/health/breakers")
async def health_breakers():
    """Snapshot of every circuit breaker the LLM chain + storage layer
    manage. Powers the admin "service health" banner so operators can see
    a degraded provider before the user does.

    Status legend:
      - closed:    healthy, requests pass through
      - half_open: probing — single request allowed
      - open:      blocking — provider quarantined
    """
    from core.circuit_breaker import redis_circuit_breaker, mongodb_circuit_breaker
    from core.llm_chain import (
        openrouter_breaker, gemini_breaker, ollama_breaker, chromadb_breaker,
    )
    breakers = [
        redis_circuit_breaker, mongodb_circuit_breaker,
        openrouter_breaker, gemini_breaker, ollama_breaker, chromadb_breaker,
    ]
    states = [b.get_state() for b in breakers]
    overall = "healthy"
    open_breakers = [s["name"] for s in states if s["state"] == "open"]
    if open_breakers:
        overall = "degraded" if len(open_breakers) < len(states) else "unhealthy"
    return {
        "overall": overall,
        "open_breakers": open_breakers,
        "breakers": states,
    }


@app.get("/metrics")
async def metrics():
    from core.circuit_breaker import redis_circuit_breaker, mongodb_circuit_breaker
    lines = []
    health_report = health_check_system.get_health_report()
    for comp_name, comp_data in health_report["components"].items():
        status_value = 1 if comp_data["status"] == "healthy" else 0
        lines.append(f'component_health{{component="{comp_name}"}} {status_value}')
        if comp_data["response_time_ms"]:
            lines.append(f'component_response_time_ms{{component="{comp_name}"}} {comp_data["response_time_ms"]}')
        if comp_data["success_rate"]:
            lines.append(f'component_success_rate{{component="{comp_name}"}} {comp_data["success_rate"]}')
    redis_state = redis_circuit_breaker.get_state()
    mongo_state = mongodb_circuit_breaker.get_state()
    lines.append(f'circuit_breaker_failures{{name="redis"}} {redis_state["failure_count"]}')
    lines.append(f'circuit_breaker_failures{{name="mongodb"}} {mongo_state["failure_count"]}')
    return "\n".join(lines) + "\n"


@app.get("/debug/config")
async def debug_config():
    if not config.is_development():
        return JSONResponse(status_code=403, content={"error": "Only available in development mode"})
    safe_config = {}
    for key, value in config._config.items():
        if key in config.SENSITIVE_VARS:
            safe_config[key] = "****"
        else:
            safe_config[key] = value
    return {
        "config": safe_config,
        "environment": config.get("ENV"),
        "is_production": config.is_production(),
        "warnings": config._warnings,
    }
