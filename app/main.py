import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import get_optional_admin_from_request
from app.config import get_settings
from app.crud import count_jobs, create_sample_job
from app.database import Base, engine
from app.redis_client import redis_client
from app.routers import admin, api, public, telegram_bot
from app.telegram_service import setup_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["is_admin_authenticated"] = get_optional_admin_from_request


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s", settings.app_name)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        total_jobs = await count_jobs(session)
        if total_jobs == 0 and settings.seed_sample_data:
            await create_sample_job(session)

    try:
        await redis_client.ping()
        logger.info("Connected to Redis")
    except Exception:
        logger.exception("Redis connection check failed")

    await setup_webhook()

    yield

    await redis_client.aclose()
    await engine.dispose()
    logger.info("Stopped %s", settings.app_name)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(public.router)
app.include_router(admin.auth_router)
app.include_router(admin.router)
app.include_router(api.router)
app.include_router(telegram_bot.router)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found."})
    return templates.TemplateResponse(
        "base.html",
        {
            "request": request,
            "page_title": "Not Found",
            "content_title": "Page not found",
            "content_body": "The page you requested does not exist.",
        },
        status_code=404,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path.startswith("/admin"):
        return RedirectResponse(url="/login", status_code=303)
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return templates.TemplateResponse(
        "base.html",
        {
            "request": request,
            "page_title": f"Error {exc.status_code}",
            "content_title": "Request failed",
            "content_body": exc.detail if isinstance(exc.detail, str) else "Something went wrong.",
        },
        status_code=exc.status_code,
    )


@app.get("/health", response_class=HTMLResponse)
async def health() -> str:
    return "ok"
