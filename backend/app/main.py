from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(
    title="FreightPrint",
    description="Multimodal freight carbon, route and risk engine",
    version="0.1.0",
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Endpoints are defined with `def` rather than `async def` on purpose: routing calls OSRM
# over blocking HTTP, and FastAPI runs sync endpoints in a threadpool. Declaring them
# async would block the event loop for the whole six seconds a cold route takes.
if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")
