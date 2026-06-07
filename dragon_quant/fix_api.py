"""
fix-api — 从目标网站自动捕获 API 请求配置

通过 playwright 无头浏览器访问真实页面，拦截网络请求，
根据响应数据结构匹配目标接口，自动提取并测试请求模板。

用法:
    python -m dragon_quant fix-api --provider eastmoney
    python -m dragon_quant fix-api --provider eastmoney --show-browser
"""

import json
import re
import time
import urllib.parse
from urllib.parse import urlparse, parse_qs

from dragon_quant.config.api_config import (
    RequestTemplate,
    save_templates,
    hardcoded_templates,
    save_single_template,
)
from dragon_quant.providers.cookie import get_em, set_em
from dragon_quant.providers.eastmoney import _parse_jsonp, _fetch as em_fetch

DYNAMIC_PARAMS = {
    "fs", "fields", "pn", "pz", "po", "fid",
    "cb", "ut", "secid", "_", "lmt", "beg", "end",
    "klt", "fqt", "np", "fltt", "invt",
}

HEADER_WHITELIST = {
    "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
    "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
    "accept", "accept-language", "user-agent",
    "pragma", "cache-control", "origin", "priority",
}

FIX_PAGES = {
    "eastmoney": [
        {
            "url": "https://quote.eastmoney.com/center/hsbk.html",
            "endpoints": [
                {
                    "name": "sector_ranking",
                    "host": "push2.eastmoney.com",
                    "path_contains": "clist/get",
                    "response_check": lambda data: (
                        isinstance(data.get("data", {}).get("diff"), list)
                        and any("f12" in d and "f14" in d and "f3" in d
                                for d in data["data"]["diff"])
                    ),
                },
            ],
        },
        {
            "url": "https://quote.eastmoney.com/bk/90.BK0883.html",
            "endpoints": [
                {
                    "name": "sector_components",
                    "host": "push2.eastmoney.com",
                    "path_contains": "clist/get",
                    "response_check": lambda data: (
                        isinstance(data.get("data", {}).get("diff"), list)
                        and any("f12" in d and "f14" in d and "f2" in d
                                for d in data["data"]["diff"])
                        and len(data["data"]["diff"]) < 200
                    ),
                },
                {
                    "name": "sector_5min_kline",
                    "host": "push2his.eastmoney.com",
                    "path_contains": "kline/get",
                    "response_check": lambda data: (
                        isinstance(data.get("data", {}).get("klines"), list)
                        and len(data["data"]["klines"]) > 0
                    ),
                },
            ],
        },
    ],
}

TEST_PARAMS = {
    "sector_ranking": {
        "fs": "m:90+t:3",
        "fields": "f12,f14,f3,f4,f8,f104",
        "fid": "f3",
        "pn": "1", "pz": "5",
        "po": "1",
        "np": "1", "fltt": "1", "invt": "2",
    },
    "sector_components": {
        "fs": "b:BK0883+f:!",
        "fields": "f12,f14,f3,f2",
        "fid": "f3",
        "pn": "1", "pz": "5",
        "po": "1",
        "np": "1", "fltt": "1", "invt": "2",
    },
    "sector_5min_kline": {
        "secid": "90.BK0883",
        "klt": "5",
        "lmt": "5",
        "fqt": "1",
        "beg": "0",
        "end": "20500101",
    },
}


def _extract_template(url_str: str, request_headers: dict, referer: str) -> RequestTemplate:
    parsed = urlparse(url_str)
    base = f"{parsed.scheme}://{parsed.hostname}"
    path = parsed.path
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat_params = {k: v[0] for k, v in params.items()}
    fixed_params = {k: v for k, v in flat_params.items() if k not in DYNAMIC_PARAMS}
    headers = {
        k: v for k, v in request_headers.items()
        if k.lower() in HEADER_WHITELIST
    }
    return RequestTemplate(
        base=base, path=path,
        fixed_params=fixed_params,
        headers=headers,
        referer=referer,
    )


def _inject_cookies(context, provider: str):
    if provider == "eastmoney":
        cookie_str = get_em()
        if not cookie_str:
            return False
        cookies = []
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies.append({
                    "name": name, "value": value,
                    "domain": ".eastmoney.com",
                    "path": "/",
                })
        if cookies:
            context.add_cookies(cookies)
        return True
    return False


def _capture_endpoints(provider: str, headless: bool = True) -> dict[str, RequestTemplate]:
    pages_config = FIX_PAGES.get(provider)
    if not pages_config:
        print(f"  ❌ 不支持的 provider: {provider}")
        return {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ❌ playwright 未安装")
        print("     pip install playwright && playwright install chromium")
        return {}

    all_templates: dict[str, RequestTemplate] = {}
    pw = sync_playwright().start()

    try:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/148.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        if not _inject_cookies(context, provider):
            print("  ❌ Cookie 未设置，请先运行:")
            print("     python -m dragon_quant.providers.cookie fetch --source em")
            context.close()
            browser.close()
            pw.stop()
            return {}

        def _test_api(page) -> bool:
            try:
                return page.evaluate("""() => {
                    return new Promise((resolve) => {
                        const ctrl = new AbortController();
                        const tid = setTimeout(() => { ctrl.abort(); resolve(false); }, 3000);
                        fetch('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14', { signal: ctrl.signal })
                        .then(r => r.text())
                        .then(t => { clearTimeout(tid); resolve(t.includes('"data":')); })
                        .catch(() => { clearTimeout(tid); resolve(false); });
                    });
                }""")
            except Exception:
                return False

        probe_page = context.new_page()
        probe_page.goto("https://quote.eastmoney.com/center/hsbk.html",
                        wait_until="domcontentloaded", timeout=15000)
        probe_page.wait_for_timeout(2000)

        if not _test_api(probe_page):
            print("\n  🔐 检测到验证码，正在打开浏览器窗口...")
            print("     请在浏览器中完成滑动验证/图片验证。")
            print("     验证通过后程序将自动继续...\n")
            context.close()
            browser.close()

            browser = pw.chromium.launch(headless=False)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            _inject_cookies(context, provider)
            page = context.new_page()
            page.goto("https://quote.eastmoney.com/center/hsbk.html",
                      wait_until="domcontentloaded", timeout=15000)

            start_time = time.time()
            while time.time() - start_time < 60:
                if _test_api(page):
                    break
                time.sleep(1)

            raw = context.cookies()
            if raw:
                cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in raw)
                if cookie_str:
                    set_em(cookie_str)

            page.close()
            context.close()
            browser.close()

            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            _inject_cookies(context, provider)

        probe_page.close()

        for page_cfg in pages_config:
            page_url = page_cfg["url"]
            endpoints = page_cfg["endpoints"]
            print(f"\n  📄 访问 {page_url}")

            page = context.new_page()
            page_referer = page_url

            captured_responses: list[tuple[str, dict, str, str]] = []

            def _on_response(response):
                try:
                    body = response.text()
                    captured_responses.append((
                        response.url,
                        response.request.headers,
                        page_referer,
                        body,
                    ))
                except Exception:
                    pass

            page.on("response", _on_response)

            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(5000)
            except Exception as e:
                print(f"     ⚠️ 页面加载异常: {e}")

            page.close()

            for ep_cfg in endpoints:
                ep_name = ep_cfg["name"]
                host = ep_cfg["host"]
                path_contains = ep_cfg["path_contains"]
                response_check = ep_cfg["response_check"]

                candidates = [
                    (url, hdrs, ref, body)
                    for url, hdrs, ref, body in captured_responses
                    if host in url and path_contains in url
                ]

                if not candidates:
                    print(f"     ❌ {ep_name}: 未在页面请求中找到匹配 {host} + {path_contains}")
                    continue

                found = False
                for url_str, req_headers, ref, body in candidates:
                    data = _parse_jsonp(body)
                    if data and response_check(data):
                        tpl = _extract_template(url_str, req_headers, ref)
                        all_templates[ep_name] = tpl
                        print(f"     ✅ {ep_name} → {tpl.base}{tpl.path}")
                        found = True
                        break

                if not found:
                    print(f"     ❌ {ep_name}: 找到 {len(candidates)} 个候选请求但响应不匹配")
                    for url_str, _, _, body in candidates[:3]:
                        data = _parse_jsonp(body)
                        if data is None:
                            print(f"        ↳ {url_str[:90]}... [非 JSONP]")
                        else:
                            top_keys = list(data.keys())
                            data_info = list(data.get("data", {}).keys()) if isinstance(data.get("data"), dict) else type(data.get("data")).__name__
                            print(f"        ↳ {url_str[:90]}... [keys={top_keys}, data={data_info}]")

        context.close()
        browser.close()
    finally:
        pw.stop()

    return all_templates


def _test_templates(provider: str, templates: dict[str, RequestTemplate]) -> bool:
    print(f"\n  🧪 测试捕获的配置...")
    all_pass = True

    for ep_name, tpl in templates.items():
        test_params = TEST_PARAMS.get(ep_name)
        if not test_params:
            print(f"     ❌ {ep_name}: 无测试参数定义")
            all_pass = False
            continue

        url = tpl.build_url(**test_params)
        ref = tpl.referer
        if "{code}" in ref and "secid" in test_params:
            ref = ref.replace("{code}", test_params["secid"].split(".")[1])

        t0 = time.time()
        data = em_fetch(url, ref, endpoint=f"fix:{ep_name}")
        elapsed = (time.time() - t0) * 1000

        if data is None:
            print(f"     ❌ {ep_name}: urllib 请求失败 ({elapsed:.0f}ms)")
            all_pass = False
        else:
            print(f"     ✅ {ep_name} ({elapsed:.0f}ms)")

    return all_pass


def fix_api(provider: str = "eastmoney", headless: bool = True):
    print(f"🔧 修复 {provider} API 配置")
    print()
    print(f"  [1/3] 启动浏览器捕获请求...")

    templates = _capture_endpoints(provider, headless=headless)

    if not templates:
        print()
        print("❌ 自动修复失败：未能捕获到接口请求。")
        print()
        print("   请尝试以下步骤：")
        print(f"   1. 确保 Cookie 有效: python -m dragon_quant.providers.cookie fetch --source em")
        print(f"   2. 确保网络可访问: curl -I https://quote.eastmoney.com/")
        print(f"   3. 如果上述步骤正常，请升级包版本:")
        print(f"        pip install --upgrade dragon-quant")
        return

    expected = {ep_cfg["name"] for pg in FIX_PAGES.get(provider, []) for ep_cfg in pg["endpoints"]}
    missing = expected - set(templates.keys())
    if missing:
        print(f"\n     ⚠️ 以下接口未捕获: {', '.join(missing)}")
        for m in missing:
            hardcoded = hardcoded_templates(provider).get(m)
            if hardcoded:
                templates[m] = hardcoded
                print(f"     ↳ {m}: 回退使用包内硬编码默认值")

    print(f"\n  [2/3] 测试捕获的配置...")
    if not _test_templates(provider, templates):
        print()
        print("❌ 自动修复失败：部分接口测试不通过。")
        print()
        print("   请升级包版本后重试:")
        print("       pip install --upgrade dragon-quant")
        print("   升级后重新运行:")
        print(f"       python -m dragon_quant fix-api --provider {provider}")
        return

    print(f"\n  [3/3] 保存配置...")
    save_templates(provider, templates)
    from dragon_quant.config.api_config import CONFIG_PATH
    print(f"     💾 {CONFIG_PATH}")
    print()
    print(f"✅ API 配置已全部修复并保存")
