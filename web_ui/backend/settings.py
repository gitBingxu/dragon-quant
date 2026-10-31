"""DashboardSettings：只做路径常量与默认值，不做任何 I/O。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ``web_ui/backend/settings.py`` → 包目录 ``web_ui``
WEB_UI_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = WEB_UI_DIR.parent
DEFAULT_DIST_DIR = WEB_UI_DIR / "dist"


@dataclass(frozen=True)
class DashboardSettings:
    """Web 仪表盘运行期设置（frozen，可安全跨请求共享）。"""

    dist_dir: Path = DEFAULT_DIST_DIR
    project_root: Path = PROJECT_ROOT
    default_source: str = "v2"
    default_page: str = "review"

    def normalized_source(self) -> str:
        return self.default_source if self.default_source in {"v1", "v2"} else "v2"

    def landing_path(self) -> str:
        return "/account" if self.default_page == "account" else "/"
