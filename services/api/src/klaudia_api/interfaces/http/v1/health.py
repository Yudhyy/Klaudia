from fastapi import APIRouter

from klaudia_api.composition.settings import get_settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.service_name,
        "version": settings.version,
    }
