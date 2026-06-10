"""
API 请求模板 — 持久化配置管理

每个 provider 下的每个 endpoint 对应一个 RequestTemplate，
封装请求 URL 拼接和 Header 构建。

配置存储路径: {DATA_DIR}/config/api_config.json
"""

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

import urllib.parse
import time

from dragon_quant.storage.paths import DATA_DIR

CONFIG_DIR = DATA_DIR / "config"
CONFIG_PATH = CONFIG_DIR / "api_config.json"

CONFIG_VERSION = "1"


@dataclass
class RequestTemplate:
    base: str
    path: str
    fixed_params: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    referer: str = ""

    def build_url(self, **dynamic_params) -> str:
        params = {**self.fixed_params, **dynamic_params}
        if "cb" not in params:
            # 尽量对齐东财前端 JSONP callback 命名，降低风控概率
            # 形如：jQuery112307663912278618489_1780992936600
            # 注：callback 名称不会影响我们 _parse_jsonp 的解析。
            ts = int(time.time() * 1000)
            pseudo_rand = int(str(ts)[-6:]) * 123457  # 仅用于仿真格式，无安全意义
            params["cb"] = f"jQuery{pseudo_rand}_{ts}"
        if "_" not in params:
            params["_"] = str(int(time.time() * 1000))
        qs = urllib.parse.urlencode(params)
        return f"{self.base}{self.path}?{qs}"

    def build_headers(self, referer: str = "", cookie: str = "") -> dict:
        headers = {}
        if self.headers:
            headers.update(self.headers)
        if referer or self.referer:
            headers["Referer"] = referer or self.referer
        if cookie:
            headers["Cookie"] = cookie
        return headers


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_templates(provider: str) -> Optional[dict[str, RequestTemplate]]:
    if not CONFIG_PATH.exists():
        return None
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        provider_data = data.get(provider, {})
        if not provider_data:
            return None
        templates = {}
        for ep_name, ep_cfg in provider_data.items():
            templates[ep_name] = RequestTemplate(
                base=ep_cfg["base"],
                path=ep_cfg["path"],
                fixed_params=ep_cfg.get("fixed_params", {}),
                headers=ep_cfg.get("headers", {}),
                referer=ep_cfg.get("referer", ""),
            )
        return templates
    except Exception:
        return None


def save_templates(provider: str, templates: dict[str, RequestTemplate]):
    _ensure_config_dir()
    existing = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    existing.setdefault("version", CONFIG_VERSION)
    provider_data = {}
    for name, tpl in templates.items():
        provider_data[name] = {
            "base": tpl.base,
            "path": tpl.path,
            "fixed_params": tpl.fixed_params,
            "headers": tpl.headers,
            "referer": tpl.referer,
        }
    existing[provider] = provider_data
    tmp_path = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def save_single_template(provider: str, endpoint: str, tpl: RequestTemplate):
    existing = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                existing = json.load(f)
        except Exception:
            existing = {}
    existing.setdefault("version", CONFIG_VERSION)
    existing.setdefault(provider, {})
    existing[provider][endpoint] = {
        "base": tpl.base,
        "path": tpl.path,
        "fixed_params": tpl.fixed_params,
        "headers": tpl.headers,
        "referer": tpl.referer,
    }
    _ensure_config_dir()
    tmp_path = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def hardcoded_templates(provider: str) -> dict[str, RequestTemplate]:
    if provider == "eastmoney":
        return {
            "sector_ranking": RequestTemplate(
                base="https://push2.eastmoney.com",
                path="/webguest/api/qt/clist/get",
                fixed_params={
                    "np": "1", "fltt": "1", "invt": "2",
                    "timil": "1", "dect": "1", "wbp2u": "|0|0|0|web",
                },
                headers={},
                referer="https://quote.eastmoney.com/center/hsbk.html",
            ),
            "sector_components": RequestTemplate(
                base="https://push2.eastmoney.com",
                path="/api/qt/clist/get",
                fixed_params={
                    "np": "1", "fltt": "2", "invt": "2",
                    "dect": "1",
                },
                headers={},
                referer="https://quote.eastmoney.com/center/gridlist.html",
            ),
            "sector_5min_kline": RequestTemplate(
                base="https://push2his.eastmoney.com",
                path="/api/qt/stock/kline/get",
                fixed_params={
                    "fqt": "1",
                    "beg": "0",
                    "end": "20500101",
                    "smplmt": "460",
                },
                headers={},
                referer="https://quote.eastmoney.com/bk/90.{code}.html",
            ),
        }
    return {}
