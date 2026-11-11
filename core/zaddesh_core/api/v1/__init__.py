from fastapi import APIRouter

from zaddesh_core.api.v1.auth import router as auth_router
from zaddesh_core.api.v1.devices import router as devices_router
from zaddesh_core.api.v1.checks import router as checks_router
from zaddesh_core.api.v1.metrics import router as metrics_router
from zaddesh_core.api.v1.alerts import router as alerts_router
from zaddesh_core.api.v1.collectors import router as collectors_router
from zaddesh_core.api.v1.system import router as system_router
from zaddesh_core.api.v1.logs import router as logs_router

from zaddesh_core.api.v1.stream import router as stream_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(devices_router)
api_v1_router.include_router(checks_router)
api_v1_router.include_router(metrics_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(collectors_router)
api_v1_router.include_router(system_router)
api_v1_router.include_router(logs_router)
api_v1_router.include_router(stream_router)

__all__ = ["api_v1_router"]

