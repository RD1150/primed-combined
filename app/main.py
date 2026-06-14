from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
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


# Pre-launch gate: if INVITE_KEY env var is set, /app and /app/* require either
# an ?invite=<KEY> query param (which sets a long-lived cookie) or the cookie
# itself. New visitors without either get redirected to the landing page.
# To disable the gate at launch, unset INVITE_KEY on Render.
@app.get("/app")
@app.get("/app/{path:path}")
async def serve_app(request: Request, path: str = ""):
    invite_key = os.environ.get("INVITE_KEY")
    if invite_key:
        if request.query_params.get("invite") == invite_key:
            response = FileResponse(os.path.join("static", "app.html"))
            response.set_cookie(
                "primed_invite", "1",
                max_age=60 * 60 * 24 * 365,
                samesite="lax",
            )
            return response
        if request.cookies.get("primed_invite") != "1":
            return RedirectResponse(url="/", status_code=302)
    return FileResponse(os.path.join("static", "app.html"))


# Serve static assets (icons, manifest, etc.)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join("static", "landing.html"))


@app.get("/demo")
async def demo():
    return RedirectResponse(url="/#demo")


@app.get("/privacy")
async def privacy():
    return FileResponse(os.path.join("static", "privacy.html"))


@app.get("/terms")
async def terms():
    return FileResponse(os.path.join("static", "terms.html"))


@app.get("/disclaimer")
async def disclaimer():
    return FileResponse(os.path.join("static", "disclaimer.html"))


@app.get("/refund")
async def refund():
    return FileResponse(os.path.join("static", "refund.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}
