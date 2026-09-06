"""ResearchFlow AI — FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.notifications import router as notifications_router
from app.api.research import router as research_router
from app.api.schedules import router as schedules_router
from app.api.schedules import scheduler_lifespan
from app.core.config import CORS_ORIGINS


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start/stop in-process components with the application.

    The scheduler (Step 7) starts here — never at module import — and
    shuts down cleanly on application shutdown.
    """
    async with scheduler_lifespan(None):
        yield


app = FastAPI(
    title="ResearchFlow AI",
    description="Autonomous AI Research Automation Platform",
    version="0.1.0",
    lifespan=lifespan,
)

# Allow the Next.js frontend (dev server) to talk to the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(research_router)
app.include_router(schedules_router)
app.include_router(notifications_router)


@app.get("/")
async def root() -> dict:
    """API root — quick check that the service is up."""
    return {
        "message": "ResearchFlow AI API is running",
        "version": "0.1.0",
    }


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "healthy"}
