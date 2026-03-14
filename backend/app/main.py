from fastapi import FastAPI
from app.api import health, upload, tasks, parse, extract, fields,match
from app.core.logger import logger
from app.db.database import init_db
from app.db import models

app = FastAPI(title="A23 Backend", version="0.1.0")

init_db()

app.include_router(health.router)
app.include_router(upload.router)
app.include_router(tasks.router)
app.include_router(parse.router)
app.include_router(extract.router)
app.include_router(fields.router)
app.include_router(match.router)

@app.get("/")
def root():
    logger.info("访问根接口 /")
    return {"msg": "backend is running"}