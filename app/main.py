from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os

from app.config import get_settings
from app.database import init_db
from app.routes import router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Primed API",
    description="Backend for Primed.today — AI conversation intelligence for real estate agents",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes under /v1
app.include_router(router, prefix="/v1")


# Serve the app HTML at /app and any /app/* deep link (React router handles the rest)
@app.get("/app")
@app.get("/app/{path:path}")
async def serve_app(path: str = ""):
    return FileResponse(os.path.join("static", "app.html"))


# Serve static assets (icons, manifest, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join("static", "landing.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}
