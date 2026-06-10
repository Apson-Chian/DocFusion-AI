from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core.env import load_project_env


load_project_env()


from .api import extract, fields, health, match, parse, tasks, trace, upload
from .core.logger import logger
from .core.paths import FRONTEND_DIR
from .db import models  # noqa: F401
from .db.database import init_db

app = FastAPI(title="DocFusion AI", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(upload.router)
app.include_router(tasks.router)
app.include_router(parse.router)
app.include_router(extract.router)
app.include_router(fields.router)
app.include_router(match.router)
app.include_router(trace.router, tags=["trace"])

init_db()


@app.middleware("http")
async def disable_frontend_cache(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if request.method == "GET" and (
        path == "/"
        or path == "/index.html"
        or path.startswith("/src/")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/api")
def api_root():
    logger.info("访问根接口 /api")
    return {"msg": "backend is running"}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
