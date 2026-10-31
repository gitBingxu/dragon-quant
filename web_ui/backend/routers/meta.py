"""``GET /api/meta``：版本、数据目录、两库是否存在、dist 是否构建。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import dashboard_settings, storage_paths
from ..settings import DashboardSettings

router = APIRouter(tags=["meta"])

ROUTE_LIST = ["/", "/scans", "/scans/:scanId", "/factors", "/prep", "/account"]


class Meta(BaseModel):
    version: str
    data_dir: str
    dragon_db: bool
    assets_db: bool
    dist_built: bool
    sources: list[str]
    default_source: str
    default_page: str
    routes: list[str]


@router.get("/api/meta", response_model=Meta)
def get_meta(settings: DashboardSettings = Depends(dashboard_settings),
             paths=Depends(storage_paths)) -> Meta:
    from dragon_quant._version import __version__

    return Meta(
        version=__version__,
        data_dir=str(paths.DATA_DIR),
        dragon_db=paths.DB_PATH.is_file(),
        assets_db=paths.ASSETS_DB_PATH.is_file(),
        dist_built=(settings.dist_dir / "index.html").is_file(),
        sources=["v1", "v2"],
        default_source=settings.normalized_source(),
        default_page=settings.default_page,
        routes=ROUTE_LIST,
    )
