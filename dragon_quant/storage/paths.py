"""
统一数据目录管理

所有持久化路径收口于此：
  DATA_DIR    — 数据根目录
  COOKIE_DIR  — Cookie 存储
  CACHE_DIR   — 接口数据缓存
  LOG_DIR     — 结构化日志 (JSONL)
  RESULTS_DIR — 扫描报告 (TXT)
  SHARED_DIR  — 子进程共享缓存快照
  DB_PATH           — dragon.db（扫描/回测主库）
  ASSETS_DB_PATH    — assets.db（原始数据资产 + 算法快照，独立库）
  LEGACY_PREP_DB_PATH — 旧 prep.db（仅 status 展示，不再读写）

跨平台兼容：macOS / Linux / Windows
"""

import os
import sys
from pathlib import Path


def _get_data_dir() -> Path:
    override = os.environ.get("DQ_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "dragon-quant"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "dragon-quant"
    else:
        base = Path(
            os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        )
        return base / "dragon-quant"


DATA_DIR = _get_data_dir()
COOKIE_DIR = DATA_DIR / "cookies"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
RESULTS_DIR = DATA_DIR / "results"
SHARED_DIR = DATA_DIR / "shared"
DB_PATH = DATA_DIR / "dragon.db"
ASSETS_DB_PATH = DATA_DIR / "assets.db"        # 数据资产独立库（prep + auction）
LEGACY_PREP_DB_PATH = DATA_DIR / "prep.db"     # 旧资产库，仅 status 展示，不再读写

for _d in [COOKIE_DIR, CACHE_DIR, LOG_DIR, RESULTS_DIR, SHARED_DIR]:
    _d.mkdir(parents=True, exist_ok=True)
