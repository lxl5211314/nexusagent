"""FastAPI application assembly: config, DB init, routes, static files."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import audit_routes, chat, eval_routes, health, knowledge, system
from app.db.base import init_db

STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="NexusAgent", version="0.3.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    init_db()

    app.include_router(health.router)
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(eval_routes.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(audit_routes.router, prefix="/api")

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="web")

    return app


app = create_app()
