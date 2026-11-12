import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from klaudia_api.interfaces.http.ratelimit import attach_rate_limiter
from klaudia_api.interfaces.http import v1_router
from klaudia_api.composition.api import KlaudiaContainer
from klaudia_api.interfaces.http.orchestrator import KlaudiaOrchestrator
from klaudia_api.composition.settings import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_production_config()
    logger.info(f"Starting {settings.service_name} v{settings.version}")

    container = await KlaudiaContainer.create(settings)
    orchestrator = KlaudiaOrchestrator(container)

    app.state.container = container
    app.state.orchestrator = orchestrator

    yield

    await container.shutdown()
    logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """Build the HTTP application without opening process resources.

    Returns:
        Application with the existing routes, limiter and lifespan.
    """
    application = FastAPI(
        title="Klaudia Chatbot",
        version="1.0.0",
        lifespan=lifespan,
    )
    attach_rate_limiter(application)
    application.include_router(v1_router)
    return application


app = create_app()
