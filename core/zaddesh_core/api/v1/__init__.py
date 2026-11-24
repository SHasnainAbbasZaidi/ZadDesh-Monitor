from fastapi import APIRouter

from zaddesh_core.api.v1.auth import router as auth_router
from zaddesh_core.api.v1.devices import router as devices_router
from zaddesh_core.api.v1.checks import router as checks_router
from zaddesh_core.api.v1.metrics import router as metrics_router
from zaddesh_core.api.v1.alerts import router as alerts_router
from zaddesh_core.api.v1.collectors import router as collectors_router
from zaddesh_core.api.v1.system import router as system_router
from zaddesh_core.api.v1.logs import router as logs_router
from zaddesh_core.api.v1.incidents import router as incidents_router
from zaddesh_core.api.v1.network_diagram import router as topology_router, worker as topology_worker, widgets as topology_widgets
from zaddesh_core.config import settings

from zaddesh_core.api.v1.stream import router as stream_router
from zaddesh_core.api.v1.zabbix import fleet_router, router as zabbix_router
from zaddesh_core.api.v1.infrastructure_monitoring import router as infrastructure_monitoring_router
from zaddesh_core.api.v1.email_alerts import router as email_alerts_router
from zaddesh_core.api.v1.integrations import router as integrations_router
from zaddesh_core.api.v1.workstations import router as workstations_router
from zaddesh_core.api.v1.dashboard_tools import router as dashboard_tools_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(auth_router)
api_v1_router.include_router(devices_router)
api_v1_router.include_router(checks_router)
api_v1_router.include_router(metrics_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(collectors_router)
api_v1_router.include_router(system_router)
api_v1_router.include_router(logs_router)
api_v1_router.include_router(incidents_router)
api_v1_router.include_router(topology_router)
api_v1_router.include_router(topology_worker)
api_v1_router.include_router(topology_widgets)
api_v1_router.include_router(stream_router)
api_v1_router.include_router(zabbix_router)
api_v1_router.include_router(infrastructure_monitoring_router)
api_v1_router.include_router(email_alerts_router)
api_v1_router.include_router(integrations_router)
api_v1_router.include_router(workstations_router)
api_v1_router.include_router(fleet_router)
api_v1_router.include_router(dashboard_tools_router)


@api_v1_router.get("/features", tags=["Add-ons"])
def features() -> dict[str, bool]:
    return {
        "incident_workspace": settings.features.incident_workspace,
        "network_diagram_beta": settings.features.network_diagram_beta,
    }

__all__ = ["api_v1_router"]

