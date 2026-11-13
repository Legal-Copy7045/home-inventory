"""Application factory and process entry point.

One process serves two listeners that share state:

* the Ingress listener (default 8099) – the web UI and API for people,
  reachable only through the Supervisor's Ingress proxy;
* the API listener (default 8100) – token-authenticated API for the
  companion Home Assistant integration on the internal network.

Neither port is published on the host.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import __version__
from .ai import AIService
from .api.admin import router as admin_router
from .api.attachments import router as attachments_router
from .api.customers import router as customers_router
from .api.lookup import router as lookup_router
from .api.maintenance import router as maintenance_router
from .api.maps import router as maps_router
from .api.parts import router as parts_router
from .api.relations import router as relations_router
from .api.replenishment import router as replenishment_router
from .api.routes import router
from .api.scans import router as scans_router
from .api.tags import router as tags_router
from .api.takeaway import router as takeaway_router
from .auth import AdminBootstrap, HaAdminDirectory, load_or_create_token
from .config import Settings
from .db import Database, run_migrations
from .events import EventBus, install_session_hooks
from .limits import DEFAULT_RATE_LIMITS, MB, BodySizeLimit, RateLimiter
from .models import Attachment, LocationMap
from .privacy import install as install_privacy
from .services import capture as capture_svc
from .services import stats as stats_svc
from .services.media import MediaStore

_LOGGER = logging.getLogger("home_inventory")
def _frontend_dir() -> Path:
    from .config import getenv

    candidates = [getenv("HINV_FRONTEND_DIR"), "/app/static", str(Path(__file__).parent / "static")]
    for c in candidates:
        if c and (Path(c) / "index.html").exists():
            return Path(c)
    return Path(__file__).parent / "static"


FRONTEND_DIR = _frontend_dir()


@dataclass
class AppState:
    settings: Settings
    db: Database
    bus: EventBus
    media: MediaStore
    ai: AIService
    admin_directory: HaAdminDirectory
    api_token: str
    version: str = __version__
    barcode_providers: list | None = None
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: list[asyncio.Task] = field(default_factory=list)
    capture_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    admin_bootstrap: AdminBootstrap = field(default_factory=AdminBootstrap)
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(DEFAULT_RATE_LIMITS))

    def reopen_database(self) -> None:
        self.db.dispose()
        self.db = Database(self.settings.database_url)
        install_session_hooks(self.db.SessionLocal, self.bus)
        install_privacy(self.db.SessionLocal)
        run_migrations(self.db.engine, self.settings.db_path, self.settings.backup_dir)


def build_state(settings: Settings, ai: AIService | None = None, barcode_providers: list | None = None) -> AppState:
    settings.ensure_dirs()
    db = Database(settings.database_url)
    run_migrations(db.engine, settings.db_path, settings.backup_dir)
    bus = EventBus()
    install_session_hooks(db.SessionLocal, bus)
    install_privacy(db.SessionLocal)
    return AppState(
        settings=settings,
        db=db,
        bus=bus,
        media=MediaStore(settings.media_dir, settings.max_upload_mb * 1024 * 1024, settings.max_image_px),
        ai=ai or AIService(settings),
        admin_directory=HaAdminDirectory(settings),
        api_token=load_or_create_token(settings),
        barcode_providers=barcode_providers,
    )


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    # Ingress renders the app inside Home Assistant's own frame.
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
        "frame-ancestors 'self'; object-src 'none'; base-uri 'self'"
    ),
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
    # Other sites can't load the App's pages, photos or data into theirs.
    "Cross-Origin-Resource-Policy": "same-origin",
}


def create_app(state: AppState, channel: str) -> FastAPI:
    app = FastAPI(
        title="Home Inventory",
        version=state.version,
        # API docs only while debugging, and never through Ingress.
        docs_url="/api/docs" if channel != "ingress" and state.settings.log_level == "debug" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if channel != "ingress" and state.settings.log_level == "debug" else None,
    )
    app.state.channel = channel
    # Share attributes with the AppState instance.
    for name in ("settings", "bus", "media", "ai", "admin_directory", "admin_bootstrap", "rate_limiter", "api_token", "version", "barcode_providers", "capture_queue"):
        setattr(app.state, name, getattr(state, name))
    app.state._app_state = state

    class _DbProxy:
        def __getattr__(self, item):
            return getattr(state.db, item)

    app.state.db = _DbProxy()
    app.state.write_lock = state.write_lock
    app.state.reopen_database = state.reopen_database
    app.include_router(router)
    for extra in (attachments_router, scans_router, lookup_router, maps_router, admin_router):
        app.include_router(extra)
    app.include_router(maintenance_router)
    app.include_router(parts_router)
    app.include_router(customers_router)
    app.include_router(relations_router)
    app.include_router(tags_router)
    app.include_router(replenishment_router)
    app.include_router(takeaway_router)

    def body_limit(path: str) -> int:
        if path.endswith("/backup/restore"):
            return state.settings.backup_max_mb * MB
        return state.settings.max_upload_mb * MB + MB  # form fields and multipart overhead

    app.add_middleware(BodySizeLimit, limit_for=body_limit)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response: Response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            # Inventory data is never kept by browsers or proxies (shared devices).
            response.headers["Cache-Control"] = "no-store"
        if response.headers.get("content-type", "").startswith("text/html") or request.url.path.endswith(("/sw.js", "/manifest.json")):
            response.headers["Cache-Control"] = "no-cache"  # updates show up at once
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        first = errors[0]["message"].removeprefix("Value error, ") if errors else "Invalid request"
        return JSONResponse({"detail": first, "errors": errors}, status_code=422)

    @app.get("/api/health", include_in_schema=False)
    def health():
        return {"status": "ok", "version": state.version}

    # The UI (static files only) is served on both listeners: through Ingress,
    # and on the API port for the companion integration's sidebar proxy used
    # on Home Assistant Container/Core installs.  Data endpoints always
    # require authentication.
    if (FRONTEND_DIR / "index.html").exists():
        # Starlette's StaticFiles resolves and confines paths itself; the UI
        # uses hash routing, so only real files and "/" (index.html) are
        # requested.  Registered last, so API routes take precedence.
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
    else:

        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"detail": "Frontend not built"}, status_code=503)

    return app


# ---------------------------------------------------------------------------
# background jobs
# ---------------------------------------------------------------------------


async def _periodic(state: AppState) -> None:
    while True:
        try:
            await asyncio.to_thread(_daily_jobs, state)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Background job failed")
        await asyncio.sleep(6 * 3600)


def _daily_jobs(state: AppState) -> None:
    db = state.db.SessionLocal()
    try:
        n = stats_svc.warranty_check(db, state.settings.warranty_notice_days)
        if n:
            _LOGGER.info("Published %s warranty expiry notice(s)", n)
        n = stats_svc.expiry_check(db, state.settings.expiry_notice_days)
        if n:
            _LOGGER.info("Published %s expiry notice(s)", n)
        from .services import maintenance_notices

        res = maintenance_notices.check(db)
        if res["sent"]:
            _LOGGER.info("Published %s maintenance due notice(s); %s part(s) added to the shopping list",
                         res["sent"], res["added_to_shopping_list"])
        # Remove photos uploaded but never attached (abandoned add-item flows).
        cutoff = datetime.now(UTC) - timedelta(days=2)
        orphans = db.scalars(
            select(Attachment).where(
                Attachment.item_id.is_(None),
                Attachment.product_id.is_(None),
                Attachment.container_id.is_(None),
                Attachment.location_id.is_(None),
                Attachment.created_at < cutoff,
                Attachment.id.not_in(select(LocationMap.photo_id)),
            )
        ).all()
        from .models import ContainerScan, Item

        for att in orphans:
            if db.scalar(select(Item.id).where(Item.primary_photo_id == att.id)) or db.scalar(
                select(ContainerScan.id).where(ContainerScan.photo_id == att.id)
            ):
                continue
            state.media.delete(att.stored_path)
            state.media.delete(att.thumb_path)
            db.delete(att)
        db.commit()
    finally:
        db.close()


async def _meter_sensors(state: AppState) -> None:
    """Read linked Home Assistant runtime sensors every 15 minutes."""
    from .ha_api import HAClient
    from .services import sensor_meters

    client = HAClient.maybe(state.settings)
    if client is None:
        return
    while True:
        await asyncio.sleep(15 * 60)
        db = state.db.SessionLocal()
        try:
            n = await asyncio.to_thread(sensor_meters.sync_all, db, client)
            if n:
                _LOGGER.info("Recorded %s meter reading(s) from Home Assistant sensors", n)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Meter sensor sync failed")
        finally:
            db.close()


async def _announce_discovery(state: AppState) -> None:
    """Tell Home Assistant (via Supervisor discovery) how to reach the API.

    Home Assistant then offers the companion integration under Settings →
    Devices & services → Discovered, pre-filled with host, port and token.
    """
    s = state.settings
    if not s.supervisor_token:
        return
    import socket

    import httpx

    host = socket.gethostname() or "local-home-inventory"
    payload = {"service": "home_inventory", "config": {"host": host, "port": s.api_port, "api_token": state.api_token}}
    for attempt in range(10):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{s.supervisor_url}/discovery",
                    json=payload,
                    headers={"Authorization": f"Bearer {s.supervisor_token}"},
                )
            if r.status_code < 400:
                _LOGGER.info("Announced the inventory API to Home Assistant (host %s, port %s)", host, s.api_port)
                return
            _LOGGER.warning("Supervisor discovery returned %s: %s", r.status_code, r.text[:200])
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Discovery attempt %s failed: %s", attempt + 1, err)
        await asyncio.sleep(min(30, 2 ** attempt))


async def _admin_setup_notice(state: AppState) -> None:
    """Print the first-run setup code at start-up when no administrator is known."""
    await asyncio.sleep(3)  # let the Home Assistant user list load first

    def check() -> None:
        state.admin_directory.is_admin("")  # refreshes availability
        with state.db.SessionLocal() as db:
            state.admin_bootstrap.available(db, state.settings, state.admin_directory)

    try:
        await asyncio.to_thread(check)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Could not check for an administrator")


async def serve(settings: Settings) -> None:
    import uvicorn

    state = build_state(settings)
    ingress_app = create_app(state, "ingress")
    api_app = create_app(state, "api")
    loop = asyncio.get_running_loop()
    state.bus.bind_loop(loop)

    level = settings.log_level.lower()
    servers = [
        uvicorn.Server(uvicorn.Config(ingress_app, host="0.0.0.0", port=settings.ingress_port, log_level=level,
                                      proxy_headers=False, server_header=False, access_log=level == "debug")),
        uvicorn.Server(uvicorn.Config(api_app, host="0.0.0.0", port=settings.api_port, log_level=level,
                                      proxy_headers=False, server_header=False, access_log=level == "debug")),
    ]
    state.tasks.append(asyncio.create_task(_periodic(state)))
    state.tasks.append(asyncio.create_task(_announce_discovery(state)))
    state.tasks.append(asyncio.create_task(_meter_sensors(state)))
    state.tasks.append(asyncio.create_task(capture_svc.worker(state)))
    state.tasks.append(asyncio.create_task(_admin_setup_notice(state)))
    _LOGGER.info(
        "Home Inventory %s started (mode=%s, ingress port %s, API port %s, UI %s, Supervisor API %s, AI %s)",
        state.version, settings.mode, settings.ingress_port, settings.api_port,
        "ok" if (FRONTEND_DIR / "index.html").exists() else f"MISSING in {FRONTEND_DIR}",
        "available" if settings.supervisor_token else "not available", state.ai.describe(),
    )
    if settings.mode == "standalone":
        _LOGGER.info(
            "Standalone mode: configure the Home Inventory integration in Home Assistant with this "
            "server's API URL (port %s) and the token stored in %s",
            settings.api_port,
            settings.token_path,
        )
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        for t in state.tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        state.db.dispose()
        _LOGGER.info("Home Inventory stopped")


def run() -> None:
    settings = Settings.load()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    for noisy in ("httpx", "httpcore", "alembic.runtime.migration"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(serve(settings))
