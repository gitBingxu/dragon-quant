# 人类友好架构（第二轮 repository-wide 重构）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 dragon-quant 的 Python 包整体迁移到 `scan / review / review_account / commands / vpa / infra` 主树，最终删除全部旧路径（唯一例外 `providers/cookie.py`），且行为、CLI、schema、golden、因子版本零变化。

**Architecture:** 每个 feature 的 `main.py` 是公共入口；`infra` 是叶子层（唯一连接/schema/路径/缓存/provider/资产/因子）；迁移期用 `infra/compat.py` 的薄 shim 保持每片可测，最终 cutover 删除 shim 与旧目录，生产代码与测试全部改到新路径。

**Tech Stack:** Python ≥3.10（stdlib only 新增）、pytest、ruff、setuptools 离线构建、node:test、codebase-memory。

**Spec:** `docs/superpowers/specs/2026-10-25-human-friendly-architecture-design.md`（批准版）。迁移顺序的架构依据：`/tmp/dragon-quant-human-arch-solution/migration-plan.md`。

## Global Constraints

- 所有 Python 命令统一 `.venv/bin/python -m ...`；所有离线、无真实网络/DB/Cookie/R2/B2、不跑 `tests/smoke`。
- 不改评分算法、策略规则、阈值、默认参数、规则顺序、CLI 15 命令/51 节点/JSON/退出码、DB schema/表名/`scan_id`/存储路径、E2E golden。
- 因子 `compute_version_info()` 搬迁前后逐项相等；`infra/factor/builtin/*.py` 搬迁 diff 只允许 import 行变化。
- 不新增第三方依赖；新增工具只用 stdlib。所有新目录必须有 `__init__.py`；所有 `main.py` 不含 `if __name__ == "__main__"`。
- `sqlite3.connect` 只允许 `infra/storage/connection.py` 与 `infra/assets/db.py` 两处（测试文件除外）。
- 唯一保留旧路径：`dragon_quant/providers/cookie.py`（仅 `-m` 入口委托）+ `dragon_quant/providers/__init__.py`（包骨架）。它不是公共 import 兼容层。
- 每个变更单元同步更新相关 `AGENTS.md` / `README.md` / 结构测试；迁移期把新路径登记到「迁移期架构」表，Task 15 统一替换主树，Task 16 删除旧路径描述。
- **不执行任何 git 命令。** 每个 Task 末尾只检查变更单元完整性；是否提交由用户明确指示。
- 不改根工具文件（`publish.sh`、`encrypt_token.sh`、`pyproject.toml` 脚本入口）、不清理 artifacts、不归档新文档。

## Review Focus

1. `dragon_quant.scan` 与包根惰性导出同名：任意导入顺序下 `from dragon_quant import scan; scan(...)` 仍可调用，且 `dragon_quant.scan` 是包本身（Task 2 测试 + Task 11 函数身份测试）。
2. B1 惰性初始化：`import dragon_quant` / `-h` / `-v` 不建目录/DB/WAL；新包 `__init__` 零 eager（Task 1 基线、Task 2/3/5/16 子进程测试）。
3. 因子源码指纹：搬迁不得改 compute 函数体；`compute_version_info()` 全量逐项相等（Task 1 fixture + Task 7）。
4. cookie 唯一例外：`python -m dragon_quant.providers.cookie` 的 I1–I4 必须继续通过；库函数改由 `infra.provider.cookie` 提供；旧文件最终不得成为 import 兼容层（Task 4 + Task 16）。
5. 最终旧路径删除与打包：所有旧包/文件不存在、`infra/compat.py` 不存在、wheel/sdist 不含旧包（Task 16）。
6. Phase F 写入顺序与失败隔离：日志→报告→扫描→龙头，日志/DB 失败隔离、报告失败上抛（Task 9 parity 测试）。
7. strategy 纯拆分等价：先整搬后拆分，函数级源码/行为等价，规则顺序与默认值不变（Task 14）。
8. 私有 patch 传播：`_connect`、`build_provider_bundle`、`trader.datetime`、`prep.legacy` 在迁移期仍改变运行时行为（Task 5/9/12/13 的 `test_legacy_shims.py`）。

---

### Task 1: S0 基线快照（schema / 因子版本 / golden）

**Files:**
- Create: `tests/fixtures/architecture_baseline.json`
- Create: `tests/test_architecture_baseline.py`
- Modify: `tests/support.py`（新增 4 个只读快照 helper）

**Interfaces:**
- Produces: `tests/support.py` 中的 `dragon_schema_snapshot(tmp_path) -> dict`, `assets_schema_snapshot(tmp_path) -> dict`,
  `factor_version_snapshot() -> dict[str, list[str]]`（`[version, source]`，与 JSON 同形）, `golden_hashes() -> dict[str, str]`。
- Fixture 结构：`{"dragon_schema": {...}, "assets_schema": {...}, "factor_versions": {...}, "golden_sha256": {...}}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_architecture_baseline.py
BASELINE = Path(__file__).parent / "fixtures" / "architecture_baseline.json"

def test_write_baseline_when_requested(tmp_path):
    if os.environ.get("DQ_BASELINE_WRITE") != "1":
        pytest.skip("baseline write is opt-in")
    BASELINE.write_text(json.dumps(_collect(tmp_path), ensure_ascii=False, indent=2, sort_keys=True))

def test_schema_factor_and_golden_match_baseline(tmp_path):
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert dragon_schema_snapshot(tmp_path) == baseline["dragon_schema"]
    assert assets_schema_snapshot(tmp_path) == baseline["assets_schema"]
    assert factor_version_snapshot() == baseline["factor_versions"]
    assert golden_hashes() == baseline["golden_sha256"]
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_architecture_baseline.py -q`
Expected: FAIL（fixture 缺失 / helper 未定义）

- [ ] **Step 3: 实现 helper 并生成 fixture**

`dragon_schema_snapshot`：`tests.support.patch_storage_dbs(tmp/"dragon.db")` 内调用 `storage.db.init_db()`，再用 `sqlite3` 读 `sqlite_master`，输出 `{table: [columns]}` 与索引名。`assets_schema_snapshot`：`assets_db._connect(path=tmp/"assets.db")` + `assets_db._ensure_schema(conn)` 后同样读取。`factor_version_snapshot`：遍历 `factors.builtin.all_specs()`，用 `factors.registry.compute_version_info(spec)` 生成 `{spec.name: [version, source]}`。`golden_hashes`：`tests/e2e/golden/**` 逐文件 sha256（相对路径排序）。`_collect(tmp_path)` 只组合上述四个 helper。所有连接必须关闭，临时文件只落 `tmp_path`。

Run: `DQ_BASELINE_WRITE=1 .venv/bin/python -m pytest tests/test_architecture_baseline.py::test_write_baseline_when_requested -q`
然后：`.venv/bin/python -m pytest tests/test_architecture_baseline.py -q`
Expected: PASS

- [ ] **Step 4: 记录真实基线**

依次运行并记录**实际通过数/哈希/行为**（不写死旧数字）：
`.venv/bin/python -m pytest tests -q --ignore=tests/e2e --ignore=tests/smoke`、
`.venv/bin/python -m pytest tests/e2e -q`、`npm test`、
`.venv/bin/ruff check dragon_quant web_ui tests`、
`.venv/bin/python tests/e2e/harness/runner.py --self-check`、
`.venv/bin/python -m build --no-isolation --outdir /tmp/dq-baseline-dist`、
`.venv/bin/python -m dragon_quant -h`、`.venv/bin/python -m dragon_quant -v`。
Expected: 除 ruff 外全部 exit 0；**S0 实际 ruff 基线 = exit 1 / 43 个存量错误**（F401 18 / E501 16 / F841 6 / E731 2 / E741 1；分布 `dragon_quant` 6 / `tests` 37 / `web_ui` 0，均为迁移前存量，Task 1 自身文件 0 条），如实记录、**不得改写成 exit 0**；清理动作统一放在 Task 15 Step 5。记录 E2E 实际用例数（2026-10-25 实测 311，后续不得低于此数）、`-h/-v` 不建 `DQ_DATA_DIR` 目录。

- [ ] **Step 5: 检查变更单元完整性**

确认只新增测试/fixture，未改生产代码；如需提交由用户明确指示。

---

### Task 2: 迁移脚手架 + 可调用 `scan` 包

**Files:**
- Create: `dragon_quant/infra/__init__.py`（零 eager）、`dragon_quant/infra/compat.py`
- Create: `dragon_quant/scan/__init__.py`、`dragon_quant/scan/scores/__init__.py`（占位）、
  `dragon_quant/scan/orchestrator/__init__.py`、`dragon_quant/scan/orchestrator/main.py`（临时转发到旧 orchestrator）
- Modify: `dragon_quant/__init__.py`
- Test: `tests/test_scan_entry_identity.py`、`tests/test_legacy_shims.py`

**Interfaces:**
- `infra.compat.alias_module(module_name: str, target: ModuleType) -> None`
- `infra.compat.NameProxyModule(ModuleType)`；`install_name_proxy(module: ModuleType, mapping: Mapping[str, str], *, local_submodules: AbstractSet[str] = frozenset()) -> None`
  - `mapping` 值是可 import 的 owner **模块名**；`__getattr__` 惰性导入并解析，失败抛 `AttributeError`；
  - `__setattr__` 只对「在 mapping 且不在 local_submodules」的名字转发到 owner 模块，其余写本地。
- `scan.__call__(*args, **kwargs)` → `scan.orchestrator.main.scan(*args, **kwargs)`；`scan.__getattr__("scan")` 返回真实函数。
- Produces: `tests/test_scan_entry_identity.py::_run_python(code, tmp_path) -> CompletedProcess`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_scan_entry_identity.py
def test_scan_is_callable_under_both_import_orders(tmp_path):
    code = """
import importlib, sys
import dragon_quant
pkg = importlib.import_module("dragon_quant.scan.scores")   # 先导入子模块
importlib.import_module("dragon_quant.scan")
assert dragon_quant.scan is importlib.import_module("dragon_quant.scan")
assert callable(dragon_quant.scan), dragon_quant.scan
print("ok")
"""
    assert _run_python(code, tmp_path).stdout.strip() == "ok"

def test_scan_package_exposes_the_real_function():
    import dragon_quant.scan as pkg
    from dragon_quant.scan.orchestrator.main import scan as real
    assert pkg.scan is real
```

`_run_python` 在 `tests/test_scan_entry_identity.py` 内定义：用 `subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT), "DQ_DATA_DIR": str(tmp_path/"data"), "HOME": str(tmp_path/"home")}, capture_output=True, text=True, timeout=120)`，并断言 `DQ_DATA_DIR` 未被创建（B1）。

```python
# tests/test_legacy_shims.py（本 Task 起，Task 16 删除）
def test_name_proxy_forwards_mapped_get_and_set():
    owner = types.ModuleType("dq_probe_owner"); owner.value = 1
    sys.modules["dq_probe_owner"] = owner
    proxy = types.ModuleType("dq_probe_proxy")
    install_name_proxy(proxy, {"value": "dq_probe_owner"}, local_submodules={"sub"})
    assert proxy.value == 1
    proxy.value = 2
    assert owner.value == 2
    proxy.sub = object()
    assert "sub" in proxy.__dict__
    with pytest.raises(AttributeError):
        proxy.missing
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_scan_entry_identity.py tests/test_legacy_shims.py -q`
Expected: FAIL（`infra.compat` / `dragon_quant.scan` 不存在）

- [ ] **Step 3: 实现脚手架**

`infra/compat.py` 只用 stdlib（`importlib`、`types`、`typing`），不 import 任何业务模块。`scan/__init__.py` 用自定义 `ModuleType` 子类覆写 `__call__`，`__getattr__("scan")` 从 `scan.orchestrator.main` 取真实函数且**不写回 `globals()`**。根 `__init__.py` 的 `__getattr__` 特判 `name == "scan"` 返回 `importlib.import_module("dragon_quant.scan")`；`run_review` 映射暂不变。临时 `scan/orchestrator/main.py` 只转发到 `dragon_quant.orchestrator.scan`（Task 11 替换为真实实现）。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_scan_entry_identity.py tests/test_legacy_shims.py tests/test_lazy_storage.py -q`
Expected: `test_lazy_storage` 中 `scan is orchestrator.scan` 断言按批准语义更新为可调用包（函数身份不再成立），其余 PASS。

- [ ] **Step 5: 文档同步**

在 `AGENTS.md` / `README.md` 的「迁移期架构」表新增：`dragon_quant/scan/`（可调用包）、`dragon_quant/infra/compat.py`（迁移期临时工具，最终删除）。不修改主树 block。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 3: `infra` 叶子（clock / models / market_calendar）

**Files:**
- Create: `dragon_quant/infra/clock.py`、`dragon_quant/infra/models.py`、`dragon_quant/infra/market_calendar.py`
- Modify: `dragon_quant/runtime/clock.py`、`dragon_quant/models/types.py`、`dragon_quant/utils/trading.py`（→ 别名 shim）
- Modify: 生产与 E2E 的 import（机械替换，见 Step 3）
- Test: `tests/test_infra_leaves.py`、`tests/test_legacy_shims.py`、`tests/e2e/test_architecture_rules.py`

**Interfaces:**
- `infra.clock`: `Clock`, `SystemClock`, `FixedClock`, `DEFAULT_STAMP_FORMAT`, `get_clock`, `set_clock`, `reset_clock`, `now`, `today`, `stamp`, `monotonic`（签名不变）。
- `infra.models`: 现有 6 个 dataclass（`KBar`/`StockInfo`/`Quote`/`SectorPerformance`/`Candidate`/`ScoreResult`）。
- `infra.market_calendar`: `build_trade_calendar`, `tdx_trade_dates`, `trade_days_between`, `is_limit_up`, `kbar_to_dict`, `find_entry_day`。

- [ ] **Step 1: 写失败测试**

```python
def test_clock_is_one_process_seam():
    from dragon_quant.infra import clock
    original = clock.get_clock()
    fixed = clock.set_clock(clock.FixedClock(datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)))
    try:
        assert clock.get_clock() is fixed
        assert clock.now(timezone.utc) == fixed.now(timezone.utc)
    finally:
        clock.set_clock(original)

def test_models_are_defined_in_infra():
    from dragon_quant.infra.models import Candidate, KBar, Quote
    assert KBar.__module__ == Candidate.__module__ == Quote.__module__ == "dragon_quant.infra.models"

def test_market_calendar_importable():
    from dragon_quant.infra.market_calendar import is_limit_up, trade_days_between
    assert callable(is_limit_up) and callable(trade_days_between)

def test_importing_infra_does_not_eager_load_subpackages():
    import subprocess, sys
    code = "import dragon_quant.infra, sys; assert 'dragon_quant.infra.provider' not in sys.modules; print('ok')"
    ...
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_infra_leaves.py -q`
Expected: FAIL（`dragon_quant.infra.clock` 不存在）

- [ ] **Step 3: 搬迁 + 机械改 import**

复制实现到 `infra/`；旧文件改为 `alias_module(__name__, <new module>)`（`runtime/clock.py`、`models/types.py`、`utils/trading.py`）。然后对 `dragon_quant/**`、`web_ui/**`、`tests/**`（排除 `infra/` 与 `tests/test_legacy_shims.py`）机械替换：
`from dragon_quant.runtime import clock` → `from dragon_quant.infra import clock`；
`from dragon_quant.runtime.clock import` → `from dragon_quant.infra.clock import`；
`from dragon_quant.models.types import` → `from dragon_quant.infra.models import`；
`from dragon_quant.utils.trading import` → `from dragon_quant.infra.market_calendar import`。
`tests/e2e/harness/runner.py` 与 `tests/e2e/injection/fake_clock.py` 同步改到 `infra.clock`（模式字符串 `"runtime"` 保留）。
`infra/market_calendar.py` 内对 `providers.xueqiu` 的函数内 import 暂保持旧路径（Task 4 改）。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_infra_leaves.py tests/test_legacy_shims.py tests/test_m9b_runtime_clock.py tests/test_m9c_runtime_clock.py -q`
Expected: PASS；`test_legacy_shims` 断言 `dragon_quant.runtime.clock is dragon_quant.infra.clock`、`dragon_quant.models.types is dragon_quant.infra.models`。

- [ ] **Step 5: 结构测试与文档**

更新 `tests/e2e/test_architecture_rules.py`：clock 期望路径改为 `dragon_quant/infra/clock.py`；`_CURRENT_TIME_INVENTORY` 中 `runtime/clock.py` 改为 `infra/clock.py`。迁移期架构表新增 3 个文件。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 4: `infra/provider`（provider / data / rate_limit / bundle）

**Files:**
- Create: `dragon_quant/infra/provider/{__init__,main,base,bundle,data,rate_limit,tdx,ths,xueqiu,tencent,macro,cookie,cookie_cli}.py`
- Modify: `dragon_quant/providers/*`、`dragon_quant/data.py`、`dragon_quant/rate_limit.py`、`dragon_quant/runtime/providers.py`（→ shim）
- Test: `tests/test_infra_provider.py`、`tests/test_legacy_shims.py`、`tests/e2e/test_provider_contract.py`、`tests/e2e/test_independent_entries.py`

**Interfaces:**
- `infra.provider.main`: `StockProvider`, `TdxProvider`, `THSProvider`, `TencentProvider`, `XueqiuProvider`, `create_providers`, `ProviderBundle`, `build_provider_bundle`。
- `infra.provider.bundle.ProviderBundle` 是唯一类定义；`infra.provider.cookie` 无 `__main__`。
- 旧 `providers/cookie.py` 迁移期：非 `__main__` 时 `sys.modules[__name__] = infra.provider.cookie`；`__main__` 时委托 `infra.provider.cookie_cli.main`。

- [ ] **Step 1: 写失败测试**

```python
def test_provider_facade_exports_the_public_contract():
    from dragon_quant.infra.provider.main import (
        ProviderBundle, StockProvider, build_provider_bundle, create_providers)
    assert len(create_providers()) == 4
    assert isinstance(build_provider_bundle.__module__, str)

def test_provider_bundle_is_defined_once():
    # AST 扫描 dragon_quant/**/*.py，class ProviderBundle 只允许 infra/provider/bundle.py
    assert sites == ["dragon_quant/infra/provider/bundle.py"]

def test_canonical_cookie_has_no_main_guard():
    source = (ROOT/"dragon_quant/infra/provider/cookie.py").read_text()
    assert "__main__" not in source
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_infra_provider.py -q`
Expected: FAIL（module 不存在）

- [ ] **Step 3: 搬迁 + shim + 改 import**

移动 8 个 provider 文件 + `bundle.py`（原 `runtime/providers.py`）+ `data.py` + `rate_limit.py`；旧路径分别用 `alias_module`。`providers/__init__.py` 改为名称代理/重导出，保持 `create_providers` 与 4 个类。（Task 16 再裁剪为包骨架。）机械替换生产/测试 import：`dragon_quant.providers.X` → `dragon_quant.infra.provider.X`（`providers.cookie` 在测试中暂保留旧路径以验证迁移期入口；Task 16 改库函数 import），`dragon_quant.runtime.providers` → `dragon_quant.infra.provider.bundle`，`dragon_quant.data` → `dragon_quant.infra.provider.data`，`dragon_quant.rate_limit` → `dragon_quant.infra.provider.rate_limit`。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_infra_provider.py tests/test_legacy_shims.py tests/test_providers_cookie.py tests/test_providers_parsing.py tests/test_macro.py tests/e2e/test_provider_contract.py tests/e2e/test_independent_entries.py -q`
Expected: PASS；旧 `-m dragon_quant.providers.cookie` I1–I4 全绿。

- [ ] **Step 5: 结构测试与文档**

`test_architecture_rules.py` 的 `ProviderBundle` 期望路径改为 `infra/provider/bundle.py`；迁移期架构表新增 `infra/provider/`。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 5: `infra/storage`（唯一连接 / schema / 路径 / 通用缓存 / manager）

**Files:**
- Create: `dragon_quant/infra/storage/{__init__,main,paths,identifiers,connection,schema,_base,dragons_rows,cache,manager}.py`
- Modify: `dragon_quant/storage/{paths,manager,_base,dragons_sql}.py`（→ shim）、`dragon_quant/cache/data_cache.py`（→ 别名）、`dragon_quant/storage/db.py`（→ 名称代理）
- Test: `tests/test_storage_single_connection.py`、`tests/test_storage_schema_registry.py`、`tests/test_legacy_shims.py`

**Interfaces:**
- `infra.storage.main`: `init_db`, `DataCache`, `StorageManager`（**无** `transaction`）。
- `infra.storage.connection`: `_connect`, `init_db`, `_ensure_schema`, `_ensure_ready`, `_lock`, `_schema_lock`, `_schema_ready`。
- `infra.storage.identifiers`: `VALID_SOURCES`, `_normalize_source`, `_tables`。
- `infra.storage.schema`: 全部 dragon.db DDL 与 `ensure_*` 分组函数。
- `infra.storage.dragons_rows`: 原 `storage/dragons_sql.py` 的行 schema/编解码。

- [ ] **Step 1: 写失败测试**

```python
def test_only_two_sqlite_connection_sites():
    # _sqlite_connect_call_sites 在测试文件内定义：AST 找 ast.Call(func=ast.Attribute(attr="connect",
    # value=ast.Name(id="sqlite3")))，返回相对仓库路径排序列表。
    sites = _sqlite_connect_call_sites(ROOT/"dragon_quant")
    assert sites == ["dragon_quant/infra/storage/connection.py",
                     "dragon_quant/storage/assets_db.py"]   # Task 6 改为 infra/assets/db.py

def test_dragon_schema_table_set_is_frozen(tmp_path):
    assert set(dragon_schema_snapshot(tmp_path)) == set(json.loads(BASELINE.read_text())["dragon_schema"])
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_storage_single_connection.py tests/test_storage_schema_registry.py -q`
Expected: FAIL

- [ ] **Step 3: 拆分 `storage/db.py` 并建 shim**

把 `storage/db.py` 拆为 `infra/storage/{connection,schema,identifiers}.py`：连接/锁/`_ensure_schema`/`init_db` 进 `connection.py`；全部 DDL、`_ensure_*_columns`、`_create_versioned_tables`、`_seed_blacklist` 进 `schema.py`；`VALID_SOURCES`/`_normalize_source`/`_tables` 进 `identifiers.py`。`infra/storage/_base.py` 转发到 `connection` 模块属性。`infra/storage/cache.py` 收纳原 `cache/data_cache.py`。`infra/storage/dragons_rows.py` 收纳原 `storage/dragons_sql.py`。
旧 `storage/db.py` 改为 `NameProxyModule`：seam 名映射到 `infra.storage.connection`/`schema`/`identifiers`；领域函数名暂映射到现 owner 模块（`storage.scans`/`dragons`/`logs`/`blacklist`/`review_account`/`signals`）。旧 `storage/paths.py`、`manager.py`、`_base.py`、`dragons_sql.py`、`cache/data_cache.py` 改为别名。机械替换 `dragon_quant.storage.paths|manager` → `dragon_quant.infra.storage.*`。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_storage_single_connection.py tests/test_storage_schema_registry.py tests/test_legacy_shims.py tests/test_storage.py tests/test_lazy_storage.py tests/test_isolation.py tests/test_suite_isolation.py -q`
Expected: PASS；`patch("dragon_quant.storage.db._connect")` 仍传播到 `infra.storage.connection._connect`。

> **验证关注项（Task 2 验收转入）**：`tests/test_lazy_storage.py::TestFirstConnection::test_concurrent_first_connections_initialize_schema_once` 在三文件组合运行时曾偶发 `database is locked`（Task 2 实测约 1/6，单独运行不复现；storage 代码当时未改动）。Task 5 必须在**存储迁移前后**针对该用例做重复组合验证（例如重复运行 `test_lazy_storage.py` + 邻近文件 ≥10 次）。若再现，先按 `superpowers:systematic-debugging` 定位连接 / 锁 / `_schema_ready` 根因，**不得预设 busy_timeout 或直接改行为**；本 Task 不扩张修复。

- [ ] **Step 5: 文档与结构**

迁移期架构表新增 `infra/storage/`；`tests/support.py` 的 `patch_storage_dbs` 改为 patch `infra.storage.connection._connect` + 旧 `storage.assets_db._connect`（Task 6 再改后者）。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 6: `infra/assets`（assets.db / schema / 镜像 / 云端同步）

**Files:**
- Create: `dragon_quant/infra/assets/{__init__,main,db,schema,dragons_mirror}.py`、`infra/assets/sync/*`（含 `backends/`）、`infra/assets/ASSETS_ARCHITECTURE.md`（移动）
- Modify: `dragon_quant/storage/{assets_db,dragons_mirror}.py`、`dragon_quant/assets_sync/*`（→ shim）
- Test: `tests/test_legacy_shims.py`、`tests/test_assets_db.py`、`tests/test_dragons_mirror.py`、`tests/test_assets_sync_*.py`、`tests/e2e/test_assets.py`

**Interfaces:**
- `infra.assets.db`: 唯一 assets.db `_connect(create=True, path=None)`、`_ensure_schema`、全部资产仓储。
- `infra.assets.schema`: `TABLE_NAMES`（15 张）+ `BASE_SCHEMA` + 迁移/必需列常量。
- `infra.assets.main`: §4 表中的公共导出。
- `infra.assets.dragons_mirror` 的源库连接必须经 `connection._connect()` 模块属性调用（不得 `from ... import _connect`）。

- [ ] **Step 1: 写失败测试**

```python
def test_assets_schema_matches_sync_table_specs():
    from dragon_quant.infra.assets import schema
    from dragon_quant.infra.assets.sync import tables
    assert set(schema.TABLE_NAMES) == {spec.table for spec in tables.TABLE_SPECS}
    assert len(schema.TABLE_NAMES) == 15

def test_mirror_reads_connection_through_module_attribute():
    source = (ROOT/"dragon_quant/infra/assets/dragons_mirror.py").read_text()
    assert "connection._connect(" in source
    assert "from dragon_quant.infra.storage.connection import _connect" not in source
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_legacy_shims.py -q`
Expected: FAIL（`infra.assets` 不存在；新断言失败）

- [ ] **Step 3: 搬迁 + shim**

`storage/assets_db.py` 拆出 `schema.py`（DDL/迁移常量）与 `db.py`（连接/仓储）；`storage/dragons_mirror.py` → `infra/assets/dragons_mirror.py`；`assets_sync/*` → `infra/assets/sync/*`。旧路径全部改为别名/子模块别名，`assets_sync/__init__.py` 重导出。机械替换生产/测试 import：`dragon_quant.storage.assets_db` → `dragon_quant.infra.assets.db`、`dragon_quant.storage.dragons_mirror` → `dragon_quant.infra.assets.dragons_mirror`、`dragon_quant.assets_sync` → `dragon_quant.infra.assets.sync`。
`tests/support.py::patch_storage_dbs` 第二条改为 patch `infra.assets.db._connect`。
`ASSETS_ARCHITECTURE.md` 移动到 `infra/assets/` 并更新内部代码路径。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_assets_db.py tests/test_dragons_mirror.py tests/test_assets_sync_*.py tests/test_legacy_shims.py tests/e2e/test_assets.py -q`
Expected: PASS；`patch("dragon_quant.assets_sync.credentials.load_config")` 与 `patch("dragon_quant.storage.dragons_mirror.backfill_dragons")` 仍生效。

- [ ] **Step 5: 文档与结构**

迁移期架构表新增 `infra/assets/`；更新 `tests/test_architecture_rules.py` 中 assets 相关路径（`_ASSETS_CLI_SERVICE` 改 `infra/assets/sync/cli_service.py`，模块名断言同步）；`_sqlite_connect` 允许站点改为 `infra/storage/connection.py` + `infra/assets/db.py`。

- [ ] **Step 6: 检查变更单元完整性**

如需提交，由用户明确指示。

---

### Task 7: `infra/factor`

**Files:**
- Create: `dragon_quant/infra/factor/{__init__,main,types,registry,engine,storage,decision,evaluation,matrix,stats,service,finance,sql,finance_store}.py` + `builtin/*`
- Modify: `dragon_quant/factors/*`、`dragon_quant/storage/{factors,finance}.py`（→ shim）
- Test: `tests/test_factors_facade.py`（改新路径）、`tests/test_factors_*.py`、`tests/e2e/test_factor.py`

**Interfaces:**
- `infra.factor.main`: 13 个批准出口 + 25 个 lazy 旧名；`__all__` 恰为 13 项；
  lazy 映射使用 `_PACKAGE = __package__`（`dragon_quant.infra.factor`）拼 owner，禁止用 `__name__`。
- 内部 SQL：`infra.factor.sql`（原 `storage/factors.py`）、`infra.factor.finance_store`（原 `storage/finance.py`）。

- [ ] **Step 1: 写失败测试**

```python
def test_infra_factor_facade_lazy_mapping_uses_package():
    import dragon_quant.infra.factor as f
    assert f.build.__module__ == "dragon_quant.infra.factor.service"
    assert f.canonical_scan.__module__ == "dragon_quant.infra.factor.engine"
    assert f.__all__ == PUBLIC_EXPORTS

def test_factor_compute_versions_unchanged():
    assert factor_version_snapshot() == json.loads(BASELINE...)["factor_versions"]
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_factors_facade.py tests/test_architecture_baseline.py -q`
Expected: FAIL（`infra.factor` 不存在）

- [ ] **Step 3: 搬迁（不改函数体）**

整体复制 `factors/*` → `infra/factor/*`；`__init__.py` 的实现迁到 `main.py` 并把 `__name__` 映射改为 `__package__`；旧 `factors/__init__.py` 安装包代理到 `infra.factor.main`，旧子模块用 `alias_module` 别名。`storage/factors.py` → `infra/factor/sql.py`，`storage/finance.py` → `infra/factor/finance_store.py`，旧路径别名。
机械替换 `dragon_quant.storage.factors` → `dragon_quant.infra.factor.sql`、`dragon_quant.storage.finance` → `dragon_quant.infra.factor.finance_store`、`dragon_quant.factors` → `dragon_quant.infra.factor`（测试与命令层同步）。`builtin/*.py` 只允许 import 行变化。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_factors_*.py tests/test_architecture_baseline.py tests/test_p1_main_acceptance.py tests/test_p2_integration.py tests/test_priority_factor.py tests/e2e/test_factor.py -q`
Expected: PASS；`compute_version_info()` 与基线逐项相等。

- [ ] **Step 5: 结构测试与文档**

`test_architecture_rules.py` 的 `_FACTOR_PACKAGE`/`_FACTOR_INIT` 改为 `dragon_quant.infra.factor` / `infra/factor/main.py`；扫描器不再把 `factors/` 当根。迁移期架构表新增 `infra/factor/`。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 8: `scan/scores` + `scan/sectors`

**Files:**
- Create: `dragon_quant/scan/sectors.py`、`dragon_quant/scan/scores/{main,aggregator,base,registry,drive,leadership,anti_drop,liquidity,absorption}.py`
- Modify: `dragon_quant/sectors.py`、`dragon_quant/scorers/*`（→ shim）
- Test: `tests/test_scorers.py`、`tests/test_sectors.py`、`tests/e2e/test_scan.py`

**Interfaces:**
- `scan.scores.main`: `evaluate`, `rank_verdicts`, `DragonVerdict`, `SCORERS`。
- `scan.sectors`: `sector_key`, `components_key`, `sector_1min_key`, `sector_5min_key`, `split_sector_key`, `category_priority`, `SECTOR_*` 常量（签名不变）。

- [ ] **Step 1: 写失败测试**

```python
def test_scores_facade_and_sector_keys():
    from dragon_quant.scan.scores.main import SCORERS, evaluate, rank_verdicts
    from dragon_quant.scan.sectors import sector_key
    assert sector_key("tdx", "gn", "880683") == "tdx:gn:880683"
    assert callable(evaluate) and callable(rank_verdicts)
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_scorers.py tests/test_sectors.py -q`
Expected: FAIL（新路径不存在）

- [ ] **Step 3: 搬迁 + shim**

复制 `sectors.py`、`scorers/*` 到新树；`scan/scores/__init__.py` 轻量重导出 `main.py`；旧 `scorers/*.py` 与 `sectors.py` 改为别名。scorer 内部 import 改为 `scan.sectors`、`infra.models`、`infra.storage.cache`（仅类型）。机械替换 `dragon_quant.scorers` → `dragon_quant.scan.scores`、`dragon_quant.sectors` → `dragon_quant.scan.sectors`（生产与测试）。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_scorers.py tests/test_sectors.py tests/test_legacy_shims.py tests/e2e/test_scan.py -q`
Expected: PASS；E2E golden 零变化。

- [ ] **Step 5: 结构测试与文档**

`test_architecture_rules.py` 的 scorer/sector 路径与包级无环测试改为新包；迁移期架构表新增 `scan/scores/`、`scan/sectors.py`。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 9: `scan/storage` + Phase F 收口

**Files:**
- Create: `dragon_quant/scan/storage/{__init__,main,scans,dragons,logs,blacklist,results}.py`
- Create: `dragon_quant/vpa/storage.py`（从 `storage/dragons.py` 分出 `upsert_vpa`/`list_vpa_analysis`）
- Modify: `dragon_quant/orchestrator.py`（Phase F 改调 `persist_scan_result`）、`dragon_quant/storage/{scans,dragons,logs,blacklist,db}.py`（→ shim/proxy）
- Modify: `dragon_quant/infra/factor/{engine,service,evaluation}.py`（只读仓储改惰性新路径）
- Test: `tests/test_scan_persist_parity.py`、`tests/test_storage.py`、`tests/e2e/test_scan.py`

**Interfaces:**
- `scan.storage.main`: `PersistResult(log_count, report_path, saved_dragons, skipped_dragons, updated_dragons, errors)`,
  `persist_scan_result(*, scan_id, scan_date_fmt, elapsed_s, top_n, candidates_n, workers, results, raw_output, log_entries, report_text, dragon_rows, source="v2", trade_calendar=None, version="") -> PersistResult`,
  `write_scan_report(*, source: str, timestamp: str, text: str) -> Path`。
- 写入顺序固定：`save_scan_logs → count_scan_logs → write_scan_report → save_scan → save_dragons`。
- 失败语义：日志/扫描/龙头失败隔离进 `errors`；报告文件失败**上抛**。

- [ ] **Step 1: 写失败测试**

```python
def test_persist_order_and_failure_isolation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "save_scan_logs", lambda *a, **k: calls.append("logs"))
    monkeypatch.setattr(main, "count_scan_logs", lambda *a, **k: 3)
    monkeypatch.setattr(main, "write_scan_report", lambda **k: calls.append("report") or tmp_path/"r.txt")
    monkeypatch.setattr(main, "save_scan", lambda *a, **k: calls.append("scan"))
    monkeypatch.setattr(main, "save_dragons", lambda *a, **k: calls.append("dragons"))
    result = main.persist_scan_result(**_payload())
    assert calls == ["logs", "report", "scan", "dragons"]
    assert result.log_count == 3

def test_log_failure_is_isolated_and_report_failure_raises(...):
    # save_scan_logs 抛异常 → result.log_count == len(log_entries)、errors 非空
    # write_scan_report 抛异常 → pytest.raises
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_scan_persist_parity.py -q`
Expected: FAIL（`scan.storage.main` 不存在）

- [ ] **Step 3: 搬迁仓储 + 实现 Phase F + 改 orchestrator**

`scan/storage/scans.py`/`dragons.py`/`logs.py`/`blacklist.py` 从旧 storage 复制，内部改用 `infra.storage._base` 转发与 `infra.storage.dragons_rows`；`vpa` 函数移入 `vpa/storage.py`。`scan/storage/results.py` 只写报告文本。`main.py` 实现模板方法与子模块重导出。旧 `storage/{scans,dragons,logs,blacklist}.py` 改为名称代理（vpa 函数分派到 `vpa.storage`）；`storage/db.py` 的领域映射改到新 owner。
`orchestrator.py` Phase F 改为组装 `dragon_rows`/`report_text` 后一次调用 `persist_scan_result`，verbose 打印仍用返回 counts/errors；5 日去重移入 `persist_scan_result`（日历由 orchestrator 传入）。
`infra/factor/{engine,service,evaluation}.py`：删除模块级 `from dragon_quant.storage import db as _db`，改为函数内 `from dragon_quant.scan.storage import main as scan_store`，`_db.` → `scan_store.`；财务/资产引用改用 `infra.factor.finance_store`/`infra.assets.db`。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_scan_persist_parity.py tests/test_storage.py tests/test_legacy_shims.py tests/test_factors_engine.py tests/test_vpa.py tests/e2e/test_scan.py -q`
Expected: PASS；scan golden 零变化；`patch("dragon_quant.storage.db.<domain>")` 仍改变行为。

- [ ] **Step 5: 结构测试与文档**

新增/更新 `test_storage_schema_registry.py` 的 dragon 表归属断言；迁移期架构表新增 `scan/storage/`、`vpa/storage.py`。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 10: `scan/logger` + `scan/cache`

**Files:**
- Create: `dragon_quant/scan/logger/{__init__,main,events,report,query}.py`、`dragon_quant/scan/cache/{__init__,main}.py`
- Modify: `dragon_quant/logging/*`（→ shim）、`dragon_quant/orchestrator.py`、`dragon_quant/review_account/data.py`
- Test: `tests/test_logging.py`、`tests/test_cache.py`、`tests/e2e/test_logs_data_storage.py`

**Interfaces:**
- `scan.logger.main`: `ScanLogger`, `LogEntry`, `ReportBuilder`, `tail_logs`, `query_logs`, `clear_logs`, `list_logs`, `log_summary`。
- `scan.cache.main.ScanCache(DataCache)`: `sector_ranking_key`, `sector_components_key`, `sector_1min_key`, `sector_5min_key`, `stock_1min_key`, `stock_5min_key`, `quotes()`；`create_scan_cache(cache_dir=None) -> ScanCache`。

- [ ] **Step 1: 写失败测试**

```python
def test_scan_cache_keys_and_type():
    from dragon_quant.infra.storage.cache import DataCache
    from dragon_quant.scan.cache.main import ScanCache, create_scan_cache
    cache = create_scan_cache(tmp_path)
    assert isinstance(cache, ScanCache) and isinstance(cache, DataCache)
    assert cache.sector_ranking_key("tdx", "hy") == "sector:ranking:tdx:hy"

def test_logger_query_uses_storage_not_sql():
    source = (ROOT/"dragon_quant/scan/logger/query.py").read_text()
    assert "sqlite3" not in source
    assert "save_scan_logs" not in source
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_logging.py tests/test_cache.py -q`
Expected: FAIL

- [ ] **Step 3: 搬迁 + 改 orchestrator 与 review_account**

`logging/logger.py` → `scan/logger/events.py`；`reporter.py` → `report.py`；`query.py` → `query.py`（内部改 `scan.storage.logs`）；`__init__` 行为进 `main.py`，旧 `logging/*` 别名。`scan/cache/main.py` 实现键方法（委托 `scan.sectors` 的键函数）与 `create_scan_cache`。orchestrator 用 `create_scan_cache()` 替换 `DataCache()`。`review_account/data.py` 改 `from dragon_quant.infra.storage.cache import DataCache`。机械替换生产/测试 `dragon_quant.logging` → `dragon_quant.scan.logger`、`dragon_quant.cache.data_cache` → `dragon_quant.infra.storage.cache`。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_logging.py tests/test_cache.py tests/test_legacy_shims.py tests/test_review_account.py tests/e2e/test_logs_data_storage.py -q`
Expected: PASS；`review_account` 不再 import `scan.cache`。

- [ ] **Step 5: 结构测试与文档**

迁移期架构表新增 `scan/logger/`、`scan/cache/`；`test_lazy_storage` 的 storage 子模块清单若含 `cache` 则按新归属更新。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 11: `scan/orchestrator` 搬入 + `scan` 真实入口

**Files:**
- Modify: `dragon_quant/orchestrator.py`（→ 别名 shim）
- Create/Replace: `dragon_quant/scan/orchestrator/main.py`（真实实现）
- Modify: `dragon_quant/scan/__init__.py`（转发真实函数）、`dragon_quant/__init__.py`（docstring）
- Test: `tests/test_scan_entry_identity.py`、`tests/e2e/test_scan.py`、`tests/test_orchestrator_helpers.py`

**Interfaces:**
- `scan.orchestrator.main.scan` 保持原 `scan()` 签名与返回 dict；`run_scan` 同签名。
- `scan.__getattr__("scan")` / `scan.__call__` 与 `scan.orchestrator.main.scan` 是**同一函数对象**。

- [ ] **Step 1: 写失败测试**

```python
def test_scan_package_call_uses_the_real_orchestrator():
    import dragon_quant.scan as pkg
    import inspect
    from dragon_quant.scan.orchestrator.main import scan as real
    assert pkg.scan is real
    assert pkg.__call__.__func__  # module subclass owns __call__
    assert list(inspect.signature(pkg.scan).parameters)[:2] == ["top_n", "candidates_n"]
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_scan_entry_identity.py -q`
Expected: FAIL（临时转发器的 `__module__`/签名不符）

- [ ] **Step 3: 搬迁**

把 `orchestrator.py` 整体移入 `scan/orchestrator/main.py`，只改 import 到新树（`scan.scores`/`scan.cache`/`scan.logger`/`scan.storage`/`scan.sectors`/`infra.*`）；旧 `orchestrator.py` 改 `alias_module`。删除临时转发器。根 `__init__` docstring 更新为「可调用包」。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_scan_entry_identity.py tests/test_orchestrator_helpers.py tests/test_legacy_shims.py tests/e2e/test_scan.py tests/e2e/test_cli_global.py -q`
Expected: PASS；`api_stats`/provider 例外与 scan golden 零变化。

- [ ] **Step 5: 结构测试与文档**

`test_architecture_rules.py` 的 scan clock 期望路径改为 `scan/orchestrator/main.py`；迁移期架构表新增 `scan/orchestrator/`。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 12: `review/` 包 + `review_account` 核心 + `prep/main` + `vpa/main`

**Files:**
- Create: `dragon_quant/review/{__init__,main}.py`（删除 `review.py`）
- Create: `dragon_quant/review_account/{main.py,storage.py}`（`storage.py` 来自 `storage/review_account.py`）
- Create: `dragon_quant/review_account/prep/main.py`（`prep/__init__.py` 改包代理）
- Create: `dragon_quant/vpa/main.py`（`vpa/__init__.py` 轻量重导出）
- Modify: `dragon_quant/storage/review_account.py`（→ 别名）、`dragon_quant/review.py`（删除）
- Test: `tests/test_review.py`、`tests/test_prep.py`、`tests/test_prep_package_boundary.py`、`tests/test_m2b_injection.py`、`tests/e2e/test_review.py`、`tests/e2e/test_prep.py`

**Interfaces:**
- `review.main`: `run_review`, `review_dragon`；包 `__init__` 代理转发 `build_trade_calendar`、`XueqiuProvider` 等 patch 面。
- `review_account.main.run_review_account`；`review_account/__init__.py` 从 `main` 重导出。
- `review_account.prep.main`: §4 的全部门面导出 + `legacy()`；`prep/__init__.py` 是包代理（`local_submodules={"service","collectors","compute","rebuild","main"}`）。
- `vpa.main`: `analyze`, `VPAReport`, `FactorResult`。

- [ ] **Step 1: 写失败测试**

```python
def test_review_package_replaces_module_and_keeps_patch_surface():
    import dragon_quant.review as review
    assert not (ROOT/"dragon_quant/review.py").exists()
    assert review.run_review.__module__ == "dragon_quant.review.main"
    assert review.build_trade_calendar is not None

def test_prep_facade_points_to_main_and_keeps_legacy():
    from dragon_quant.review_account import prep
    assert prep.run_prep.__module__ == "dragon_quant.review_account.prep.main"
    assert callable(prep.legacy)
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_review.py tests/test_prep_package_boundary.py -q`
Expected: FAIL

- [ ] **Step 3: 搬迁**

`review.py` → `review/main.py`；建 `review/__init__.py` 包代理（mapping: `run_review`/`review_dragon`/`build_trade_calendar`/`XueqiuProvider`/`kbar_to_dict`/`find_entry_day` → `dragon_quant.review.main` 或 `infra` 模块；`local_submodules={"main"}`）。`review/main.py` 内 `db.get_pending_dragons`/`update_dragon_review` 改为明确的 `dragon_store` / `vpa_store` 局部名，并迁移 `tests/test_review.py` 的 patch 目标为 `review.main.dragon_store.*` / `vpa.storage.*`。`review_account/service.py` 的 `db.*` 改为 `account_store.*`，并迁移 `tests/test_m2b_injection.py` 的 patch 目标。
`storage/review_account.py` → `review_account/storage.py`，旧路径别名。`prep/__init__.py` 的实现移入 `main.py`，`__init__` 安装包代理并保留全部旧属性。`vpa/__init__.py` 从 `main.py` 重导出。机械替换 `dragon_quant.review_account.prep` 的内部 import 到 `...prep.main`（保留旧 patch 面）。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_review.py tests/test_prep.py tests/test_prep_package_boundary.py tests/test_m2b_injection.py tests/test_legacy_shims.py tests/test_review_account.py tests/e2e/test_review.py tests/e2e/test_prep.py tests/e2e/test_blacklist_dragons_vpa.py -q`
Expected: PASS；`prep.legacy` 全部旧 patch 目标仍真实生效。

- [ ] **Step 5: 文档与结构**

`STRATEGY.md` 暂不动（Task 14）；`test_architecture_rules.py` 的 prep/review 路径更新；迁移期架构表新增 `review/`、`review_account/main.py`、`review_account/storage.py`、`vpa/main.py`。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 13: `review_account/auction` + `review_account/live_trade`

**Files:**
- Create: `dragon_quant/review_account/auction/*`（`service.py` 退役）、`dragon_quant/review_account/live_trade/*`（含 `storage.py`）
- Modify: `dragon_quant/auction/*`、`dragon_quant/live_trade/*`、`dragon_quant/storage/signals.py`（→ shim）、`dragon_quant/review_account/priority.py`（auction import）
- Test: `tests/test_auction_*.py`、`tests/test_live_trade.py`、`tests/e2e/test_auction.py`、`tests/e2e/test_buy_sell.py`

**Interfaces:**
- `review_account.auction.main`: `run_auction`, `run_auction_recompute`, `format_report`, `AuctionOutcome`, `AuctionVerdict`, `evaluate`, `rank_auctions`, `_score_auction_assets`, `_trade_calendar`。
- `review_account.live_trade.main`: `run_buy`, `run_sell`；`review_account/live_trade/storage.py` 接管 `buy_sell_signals`。
- `review_account.priority` 改 import `review_account.auction.registry`（叶子）。

- [ ] **Step 1: 写失败测试**

```python
def test_auction_service_is_retired_and_main_is_the_facade():
    assert not (ROOT/"dragon_quant/auction/service.py").exists()
    from dragon_quant.review_account.auction import main
    assert callable(main.run_auction) and callable(main.run_auction_recompute)

def test_live_trade_storage_owns_signals():
    from dragon_quant.review_account.live_trade import storage
    assert callable(storage.insert_signal)
```

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_live_trade.py tests/test_auction_service.py -q`
Expected: FAIL

- [ ] **Step 3: 搬迁**

`auction/*` → `review_account/auction/*`，把 `__init__.py` 与 `service.py` 的门面合并为 `main.py`；`collect.py` 对 `review_account.market/models` 保持函数内 import；`data` import 改 `infra.provider.data`；`registry` 保持叶子。旧 `auction/*` 子模块别名 + 包代理。`live_trade/*` → `review_account/live_trade/*`，`storage/signals.py` → `live_trade/storage.py`；旧 `live_trade/*` 别名 + 包代理（`trader.datetime` 同一模块对象）。机械替换生产/测试 import。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_auction_pool.py tests/test_auction_report.py tests/test_auction_serialize.py tests/test_auction_service.py tests/test_auction_strength.py tests/test_live_trade.py tests/test_legacy_shims.py tests/e2e/test_auction.py tests/e2e/test_buy_sell.py -q`
Expected: PASS；`patch("dragon_quant.live_trade.trader.datetime")` 仍生效；`auction.service.*` patch 迁移到 `review_account.auction.main.*`。

- [ ] **Step 5: 结构测试与文档**

`test_architecture_rules.py` 的 auction 路径改新树；迁移期架构表新增两个子包。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 14: `review_account/strategy` 包化 + 纯拆分

**Files:**
- Create: `dragon_quant/review_account/strategy/{__init__,main,common,buy,checks,sell,execution,priority,risk}.py`、`strategy/STRATEGY.md`（移动）
- Create: `tests/fixtures/strategy_functions_baseline.json`（14a 生成，14b 比对）
- Delete: `dragon_quant/review_account/strategy.py`；`{execution,priority,risk}.py` 保留为别名 shim
- Modify: 消费者 import（`service.py`/`market.py`/`engine.py`/`live_trade/*`/`evaluation.py`）
- Test: `tests/test_strategy_split.py`、`tests/test_review_account.py`、`tests/test_buy_checks.py`、`tests/test_priority*.py`、`tests/e2e/test_review_account.py`

**Interfaces:**
- `strategy/main.py` 只重导出 §4 公共面 + 私有测试名；不含规则实现。
- `tests/test_strategy_split.py::strategy_function_hashes() -> dict[str, str]`：对 `strategy.main` 导出的每个公开/私有函数取 `inspect.getsource` + `ast.dump` 的 sha256；`PUBLIC_NAMES` 取 spec §4 的 strategy 行。
- 文件映射严格按 `/tmp/dragon-quant-human-arch-solution/migration-plan.md` §3：`common`（RuleResult/BuyRejected/_safe_rule/_payload/_buy/_sell/_ctx）、`buy`（entry_veto/evaluate_buy/四类买点规则）、`checks`（BUY_CHECK_*/诊断）、`sell`（SellContext/SellRule/10 条规则/evaluate_sell）。
- 唯一 patch 迁移：`strategy.entry_veto` → `strategy.buy.entry_veto`。

- [ ] **Step 1: 写失败测试（14a 前先记录基线）**

```python
# tests/test_strategy_split.py
def test_public_surface_is_complete():
    import dragon_quant.review_account.strategy as s
    for name in PUBLIC_NAMES:
        assert hasattr(s, name), name

def test_main_has_no_rule_implementations():
    source = (ROOT/"dragon_quant/review_account/strategy/main.py").read_text()
    assert "def evaluate_sell(" not in source
    assert "def evaluate_buy(" not in source

def test_split_functions_match_pre_split_source():
    assert strategy_function_hashes() == json.loads(STRATEGY_BASELINE.read_text())
```

`strategy_function_hashes()`：`inspect.getsource` + `ast.dump`（按函数名排序）；基线在 14a 生成（`DQ_STRATEGY_BASELINE_WRITE=1` 的 pytest 用例，规则同 Task 1）。

- [ ] **Step 2: 14a 整文件搬 + 记录基线**

Run: `.venv/bin/python -m pytest tests/test_strategy_split.py -q`
Expected: FAIL（`strategy` 仍是模块 / main.py 不存在）
把 `strategy.py` 移入 `strategy/main.py`，建 `strategy/__init__.py` 全量重导出（含私有测试名）；生成函数基线；跑
`.venv/bin/python -m pytest tests/test_strategy_split.py tests/test_review_account.py tests/test_review_account_timing.py tests/test_buy_checks.py tests/test_priority.py tests/test_priority_factor.py tests/e2e/test_review_account.py -q`
Expected: PASS。

- [ ] **Step 3: 14b 纯拆分**

按映射表把 `main.py` 的函数/常量原样剪切到 `common/buy/checks/sell`；`main.py` 只 `from .x import ...` 重导出。移动 `execution/priority/risk` 进包，旧文件 `alias_module`。更新消费者 import 到 `review_account.strategy.{execution,priority,risk}`；`strategy.entry_veto` patch 改 `strategy.buy.entry_veto`。`STRATEGY.md` 移到 `strategy/` 并更新内部代码路径。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_strategy_split.py tests/test_review_account.py tests/test_review_account_timing.py tests/test_buy_checks.py tests/test_priority.py tests/test_priority_factor.py tests/test_live_trade.py tests/test_legacy_shims.py tests/e2e/test_review_account.py tests/e2e/test_buy_sell.py -q`
Expected: PASS；函数源码哈希与 14a 基线一致。

- [ ] **Step 5: 文档与结构**

更新 README/AGENTS/SKILL 的 `STRATEGY.md` 路径；`test_architecture_rules.py` 的策略路径更新；迁移期架构表新增 `review_account/strategy/`。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 15: `commands` / `app` / `web_ui` 改道 + 文档/结构门禁

**Files:**
- Create: `tests/test_dependency_matrix.py`
- Modify: `dragon_quant/commands/*.py`（14 个 handler + `__init__.py`）、`dragon_quant/app.py`、`dragon_quant/cli.py`、`web_ui/server.py`
- Modify: `AGENTS.md`、`README.md`、`skills/dragon-quant/SKILL.md`、`archived_md/README.md`（仅索引行）、`pyproject.toml` ruff per-file-ignores、`tests/test_docs_consistency.py`、`tests/e2e/test_architecture_rules.py`
- Modify: 迁移后仍残留 lint 错误的文件（Step 5 机械清理，行为等价；不得靠扩大 ignore 掩盖）
- Test: `tests/test_commands.py`、`tests/test_app.py`、`tests/test_docs_consistency.py`、`tests/test_dependency_matrix.py`、`tests/e2e/test_coverage_map.py`、`tests/e2e/test_web_api.py`、`tests/test_web_server_lifecycle.py`

**Interfaces:**
- `commands` 只 import 新公共入口：`scan.orchestrator.main`、`review.main`、`review_account.main`、`review_account.prep.main`、`review_account.auction.main`、`review_account.live_trade.main`、`infra.factor`（+ `service`）、`infra.assets.main`、`vpa.main`、`scan.storage`、`scan.logger`、`infra.storage.manager`、`infra.provider.data/cookie`。
- `app`：`infra.clock`、`infra.provider.bundle`、`infra.storage.paths/connection`；仍不 import `cli`/`commands`、不构造 feature 对象。
- `web_ui.server`：`scan.storage`、`review_account.main/storage/evaluation`、`infra.provider.xueqiu`、`infra.storage.connection.init_db`。

- [ ] **Step 1: 写失败测试**

```python
def test_commands_import_only_new_public_entries():
    forbidden = ("dragon_quant.storage", "dragon_quant.providers.", "dragon_quant.runtime",
                 "dragon_quant.models", "dragon_quant.utils", "dragon_quant.factors",
                 "dragon_quant.auction", "dragon_quant.live_trade", "dragon_quant.assets_sync",
                 "dragon_quant.scorers", "dragon_quant.cache", "dragon_quant.logging",
                 "dragon_quant.orchestrator", "dragon_quant.data", "dragon_quant.sectors")
    # AST 扫描 commands/*.py，命中即失败（cookie 入口由 Task 16 断言）
    assert offenders == {}

def test_docs_describe_the_new_tree_first():
    block = test_docs_consistency._tree_block(ROOT/"AGENTS.md")   # 复用现有 helper
    assert "scan/" in block and "infra/" in block
    assert "scorers/" not in block and "    storage/" not in block

# tests/test_dependency_matrix.py
def test_infra_has_no_module_level_feature_imports():
    # AST 只取模块级语句（函数内 lazy import 不计）；factor 的只读仓储例外必须是函数内。
    assert module_level_edges("infra", target_prefixes=("scan", "review", "review_account", "vpa")) == []

def test_forbidden_feature_edges_and_no_cycles():
    # scan.storage→logger/orchestrator/scores/cache；scan.logger→orchestrator/scores/cache；
    # scan.cache→orchestrator/review_account/review；review_account→scan.scores/orchestrator/cache
    assert forbidden_edges() == []
    assert package_cycle("dragon_quant.scan") is None
    assert package_cycle("dragon_quant.infra") is None
```

`module_level_edges` / `forbidden_edges` / `package_cycle` 在 `tests/test_dependency_matrix.py` 内实现：前两者对 `dragon_quant/**/*.py` 做 AST 模块级语句扫描（函数内 lazy import 不计），后者复用 `tests/e2e/test_architecture_rules.py::_package_cycle` 的环检测逻辑。

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_docs_consistency.py tests/test_commands.py -q`
Expected: FAIL

- [ ] **Step 3: 改道 + 文档**

逐文件替换 commands 的 import（清单见 Files/Interfaces）；`commands/_shared.py` 的 `clock` 改 `infra.clock`；`commands/storage.py` 改 `infra.storage.manager`；`commands/data.py` cookie 改 `infra.provider.cookie`。`app.py`/`cli.py`/`web_ui/server.py` 同步。重写 AGENTS/README 的**第一个含 `dragon_quant/` 的 fenced block** 为最终主树；旧 shim 只放在紧随其后的「迁移期兼容路径」表格（不要第二个树 block）。更新 `test_docs_consistency.py` 的 required 路径与 `test_architecture_rules.py` 的 feature 路径/时间清单；`pyproject.toml` per-file-ignores 改新文件。

- [ ] **Step 4: 运行验证通过**

Run: `.venv/bin/python -m pytest tests/test_commands.py tests/test_app.py tests/test_docs_consistency.py tests/test_dependency_matrix.py tests/e2e/test_architecture_rules.py tests/e2e/test_coverage_map.py tests/e2e/test_cli_global.py tests/e2e/test_web_api.py tests/test_web_server_lifecycle.py -q`
Expected: PASS；51 节点与 15 命令不变；`-h`/`-v` 正常。

- [ ] **Step 5: 清理迁移后仍存在的 lint 错误（最终全量 ruff 门禁前置）**

S0 基线实测 `ruff check dragon_quant web_ui tests` = **exit 1 / 43 个存量错误**（F401 18 / E501 16 / F841 6 / E731 2 / E741 1；分布 `dragon_quant` 6 / `tests` 37 / `web_ui` 0，见 Task 1 Step 4）。迁移中**被删除/改名的文件**（`review_account/strategy.py`、`review_account/{execution,priority,risk}.py`、旧包 shim 等）其错误会随结构自然消失；**迁移后仍存在的错误必须在本步机械清理**，使 Task 16 的全量 ruff 达到 exit 0。

Run: `.venv/bin/python -m ruff check dragon_quant web_ui tests --statistics`
Expected: 列出剩余错误清单；按下述规则逐条修复后重跑，直到 `All checks passed!`

清理规则（行为等价，不改业务逻辑与字符串语义）：
- F401 unused-import：先确认不是 patch 目标或公共重导出；确属未使用才删除 import。
- **F841 unused-variable（Task 14 验收强制）**：删除前必须先检查赋值右侧（RHS）是否纯、无副作用（函数调用 / 属性写 / 全局写 / 惰性导入副作用一律视为有副作用），并运行该文件对应的行为测试。只有**证明无副作用且行为等价**后才允许删除赋值；删除后必须记录该函数的精确 AST 差异原因（函数名 / 原行 / 差异节点），且只对该函数更新 `tests/fixtures/strategy_functions_baseline.json`。若有副作用或语义不确定，**停止并上报 main**，不得强删。
- E731 lambda-assignment：改写为等价的 `def`。
- E741 ambiguous-variable：局部变量重命名为语义清晰的名字。
- E501 line-too-long：按项目 120 列换行。**纯格式化修复必须保持函数 AST 哈希不变**；若修复必然改变 AST（例如 docstring 文本重排、字符串内容变化），不得当作纯格式化处理——记录精确差异原因并先上报 main 取得确认。
- **基线纪律**：禁止批量刷新 `strategy_functions_baseline.json`；禁止借 lint 清理改写其他函数或顺带调整阈值/字符串。每一条基线变更都必须逐函数说明 AST 差异原因，并在本步验证其余函数哈希仍然逐项相等。

**禁止掩盖**：不得通过新增/扩大 `pyproject.toml` 的 per-file-ignores、新增 `# noqa` 批量标注，或 `--fix --unsafe-fixes` 来让门禁变绿；既有 per-file-ignores 只允许随文件搬迁改路径，不得新增键或扩大覆盖范围。

Run: `.venv/bin/python -m pytest tests -q --ignore=tests/e2e --ignore=tests/smoke`（清理触及文件所属测试必跑；若改动生产代码，另跑 `.venv/bin/python -m pytest tests/e2e -q`）
Expected: PASS，用例数与 Task 1 基线一致或更高（单测 1688 passed + 684 subtests、E2E 311）。

Run: `.venv/bin/python -m ruff check dragon_quant web_ui tests`
Expected: `All checks passed!`（exit 0），且人工核对 `pyproject.toml` 的 `[tool.ruff.lint.per-file-ignores]` 仅发生路径更新（条目数量与语义不变，不新增键；本计划全程不执行 git 命令）。

- [ ] **Step 6: 检查变更单元完整性** — 如需提交由用户明确指示。

---

### Task 16: 最终旧路径删除 + 全量复核

**Files:**
- Delete: `dragon_quant/{orchestrator,data,rate_limit,sectors,review}.py`、`dragon_quant/{cache,logging,scorers,storage,auction,factors,assets_sync,live_trade,models,runtime,utils}/`、
  `review_account/{strategy.py,execution.py,priority.py,risk.py}`、`dragon_quant/infra/compat.py`
- Keep/Modify: `dragon_quant/providers/__init__.py`（空包骨架）、`dragon_quant/providers/cookie.py`（仅 `__main__` 委托）
- Modify: 所有残留旧 import/patch（生产与测试）、`tests/support.py` 基线 helper、文档、`tests/e2e/test_architecture_rules.py`
- Create: `tests/test_cutover_no_legacy_paths.py`
- Test: 全量

**Interfaces:**
- `providers/cookie.py` 最终内容只有：
  ```python
  if __name__ == "__main__":
      from dragon_quant.infra.provider.cookie_cli import main
      raise SystemExit(main())
  ```
  导入该模块不得获得 `get_xq`/`set_xq` 等库函数；库测试改用 `infra.provider.cookie`。
- 其余旧路径一律不可导入。

- [ ] **Step 1: 写失败测试**

```python
FORBIDDEN = [
    "orchestrator.py", "data.py", "rate_limit.py", "sectors.py", "review.py",
    "cache", "logging", "scorers", "storage", "auction", "factors",
    "assets_sync", "live_trade", "models", "runtime", "utils",
    "review_account/strategy.py", "review_account/execution.py",
    "review_account/priority.py", "review_account/risk.py", "infra/compat.py",
]

def test_legacy_paths_are_gone():
    assert [p for p in FORBIDDEN if (ROOT/"dragon_quant"/p).exists()] == []
    assert sorted(p.name for p in (ROOT/"dragon_quant/providers").glob("*.py")) == ["__init__.py", "cookie.py"]

def test_no_production_import_of_legacy_paths():
    offenders = _ast_legacy_imports(ROOT/"dragon_quant", ROOT/"web_ui")
    assert offenders == {}

def test_legacy_modules_are_unimportable_in_fresh_process(tmp_path):
    # 隔离子进程：避免前序 pytest 测试已把旧包放进 sys.modules 造成假通过/假失败。
    code = """
import importlib, importlib.util
for name in ("dragon_quant.infra.compat", "dragon_quant.storage",
             "dragon_quant.factors", "dragon_quant.runtime", "dragon_quant.auction"):
    try:
        importlib.import_module(name)
    except ModuleNotFoundError:
        continue
    raise AssertionError(f"{name} still importable")
assert importlib.util.find_spec("dragon_quant.providers.cookie") is not None
print("ok")
"""
    assert _run_isolated(code, tmp_path).stdout.strip() == "ok"

def test_providers_cookie_is_entry_only():
    spec = importlib.util.find_spec("dragon_quant.providers.cookie")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert not hasattr(module, "get_xq")
```

`_run_isolated(code, tmp_path)` 在 `tests/test_cutover_no_legacy_paths.py` 内定义，语义与 Task 2 的 `_run_python` 相同（`sys.executable -c`、`cwd=ROOT`、`PYTHONPATH=ROOT`、隔离 `DQ_DATA_DIR`/`HOME`、`timeout=120`）。

- [ ] **Step 2: 运行验证失败**

Run: `.venv/bin/python -m pytest tests/test_cutover_no_legacy_paths.py -q`
Expected: FAIL（旧路径仍存在）

- [ ] **Step 3: 执行删除与残留改道**

删除 Files 列出的旧路径与 `infra/compat.py`；把 `tests/test_legacy_shims.py` 删除（其职责由 `test_cutover_no_legacy_paths.py` 与各新路径测试接替）。全仓 grep `dragon_quant.(orchestrator|data|rate_limit|sectors|cache|logging|scorers|storage|auction|factors|assets_sync|live_trade|models|runtime|utils)` 与 `review_account.(strategy|execution|priority|risk)` 的旧模块引用（注意区分 `scan.storage` / `infra.storage`），逐条改到新路径。`tests/test_lazy_storage.py` 的 `storage` 子模块/`__all__` 断言改到 `infra.storage` 与 `scan.storage` 边界，保留 B1 惰性断言（不得删除该测试）。`tests/support.py` 的 baseline helper 改为 patch `infra.storage.connection._connect` / `infra.assets.db._connect`。`providers/cookie.py` 裁剪为入口 stub；`providers/__init__.py` 清空为 docstring。库测试 import 改 `infra.provider.cookie`/`infra.provider.cookie_cli`。
文档：删除「迁移期架构」表与所有旧路径当前导航；保留 cookie 例外说明；历史 `archived_md` 正文不改，只更新 `archived_md/README.md` 索引行。

- [ ] **Step 4: 运行全量门禁**

```bash
.venv/bin/python -m pytest tests -q --ignore=tests/e2e --ignore=tests/smoke
.venv/bin/python -m pytest tests/e2e -q
npm test
.venv/bin/ruff check dragon_quant web_ui tests
.venv/bin/python tests/e2e/harness/runner.py --self-check
.venv/bin/python -m pytest tests/e2e/test_harness_safety.py tests/e2e/test_architecture_rules.py -q
.venv/bin/python -m build --no-isolation --outdir /tmp/dq-final-dist
.venv/bin/python -m dragon_quant -h
.venv/bin/python -m dragon_quant -v
.venv/bin/python -m pytest tests/test_cutover_no_legacy_paths.py tests/test_architecture_baseline.py -q
```
Expected: 全部 exit 0（含 `ruff check` exit 0 —— 该门禁依赖 Task 15 Step 5 完成存量 lint 清理）；E2E 用例数 ≥ Task 1 记录值（2026-10-25 实测 311）；golden/schema/factor version 与基线一致。cookie 入口只通过现有 `tests/e2e/test_independent_entries.py` 的 I1–I4 验证（已包含在上面的全量 E2E 中），**不新增任何直接业务命令**。

- [ ] **Step 5: 离线打包内容检查**

```bash
.venv/bin/python - <<'PY'
import zipfile, glob
wheel = sorted(glob.glob("/tmp/dq-final-dist/*.whl"))[-1]
names = zipfile.ZipFile(wheel).namelist()
assert not [n for n in names if n.startswith(("dragon_quant/scorers/", "dragon_quant/storage/",
    "dragon_quant/factors/", "dragon_quant/auction/", "dragon_quant/live_trade/",
    "dragon_quant/assets_sync/", "dragon_quant/runtime/", "dragon_quant/models/",
    "dragon_quant/utils/", "dragon_quant/cache/", "dragon_quant/logging/"))]
for required in ("dragon_quant/scan/__init__.py", "dragon_quant/infra/factor/main.py",
                 "dragon_quant/infra/assets/schema.py", "dragon_quant/providers/cookie.py"):
    assert required in names, required
print("wheel ok", len(names))
PY
```
Expected: 打印 `wheel ok`；sdist 同表检查。

- [ ] **Step 6: codebase-memory 与最终报告**

`index_repository` 重建后用 `get_architecture` / `query_graph` 抽查 `scan → infra`、`review_account → infra`、`infra` 无 feature 反向边；生成最终目录差异（新主树 / cookie 例外 / 已消失路径 / 旧路径引用清零）。报告真实命令输出摘要；若有未决项，登记而非继续改结构。

- [ ] **Step 7: 检查变更单元完整性** — 如需提交由用户明确指示。

---

## 执行顺序与回滚

- 依赖硬约束：Task 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16。
- 每个 Task 是独立可验收的变更单元；迁移期 shim 让相邻状态都可运行。回滚 = 回退该单元的文件变更（由用户决定是否执行）。
- 若某片验证失败，停在该片修复；不得跳过门禁进入下一片。
