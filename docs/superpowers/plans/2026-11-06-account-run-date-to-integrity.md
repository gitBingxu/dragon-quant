# 账户回测 run.date_to 完整性修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每条 `review_account_runs` 行的 `date_to` 永远等于该 run 最后一条快照的 `trade_date`，从而增量回测不再报「基准记录快照与结束日期不一致」。

**Architecture:** 全量回测在落库前把结束日收敛到模拟器实际产出的最后快照日，并把原始请求日写入 `run_meta`；`_persist_result` 作为唯一落库收口点做二次断言；run 行与全部子表改为同一 SQLite 事务提交，避免半写脏数据。

**Tech Stack:** Python 3.10+ / sqlite3 / unittest + pytest；不新增依赖。

**Spec:** `docs/solutions/2026-11-03-account-incremental-run.md`（增量契约）与本次根因诊断（见对话 / 本计划背景）。

## Global Constraints

- 项目代码零 `import pandas`；新增依赖需 MIT 兼容且同步 AGENTS.md / README。
- 域错误用中文 `ValueError`，Web 层映射 400。
- 文档同步：同一 commit 更新 `docs/agent/trading.md` 与设计文档。
- Python 3.10；`import dragon_quant` 不得有存储副作用。
- 提交风格：中文 + emoji 前缀；分支/提交由用户控制（本次只改代码，不提交）。

## Background

实库 `dragon.db` 证据：run #8 `date_to=2026-11-02` 但最后快照 `2026-10-30`；#4/#5/#6 同类。根因：
`run_review_account` 把调用方 `date_to` 原样落库；`MarketData.calendar` 在 15:05 前 / 非交易日不含当日，模拟器静默截断；全量路径没有终点校验。

## Review Focus

- `date_to` 是今天 15:05 前 / 周末 / 节假日：应钳制到最后已收盘交易日，不报错。
- 历史交易日数据缺失导致截短：本次接受「钳制 + run_meta 留痕」，由后续增量补齐（不新增网络日历依赖）。
- 子表写入中途失败：run 行必须整体回滚，不得留下空/半快照 run。
- 增量路径既有严格语义（`_assert_snapshot_end`）不得被钳制逻辑削弱。
- 旧脏记录：一次性把 `date_to` 修正为最后快照日，不删除任何 run。

---

### Task 1: 落库原子化（storage）

**Files:**
- Modify: `dragon_quant/review_account/storage.py`
- Test: `dragon_quant/tests/test_review_account_run_integrity.py`

**Interfaces:**
- Produces: `create_review_account_run(..., children: Optional[dict] = None) -> int`
  - `children = {"snapshots": list[dict], "trades": list[dict], "positions": list[dict], "events": list[dict]}`
  - 传入 `children` 时，run 行与全部子行在**同一 connection / 同一 commit** 内写入；任何异常 `rollback()` 后重新抛出。
- `save_review_account_results` 保持原签名与行为（兼容既有测试 / 调用方）。

- [ ] Step 1: 写失败测试：patch `storage._insert_children` 抛 `RuntimeError`，调用 `create_review_account_run(children=...)`，断言 `query_review_account_runs` 为空且快照表为空。
- [ ] Step 2: 运行测试，确认失败（当前无 `children` 参数）。
- [ ] Step 3: 抽出 `_insert_run(conn, ...)` 与 `_insert_children(conn, run_id, ...)`；`create_review_account_run` 增加 `children`，成功一次 `commit()`，异常 `rollback()`。
- [ ] Step 4: 运行测试，确认通过；再跑既有 storage 相关测试。

### Task 2: 全量回测钳制 + 落库不变量（service）

**Files:**
- Modify: `dragon_quant/review_account/service.py`
- Test: `dragon_quant/tests/test_review_account_run_integrity.py`

**Interfaces:**
- Produces: `resolve_persisted_date_to(result: dict, requested_date_to: str) -> str`
  - 无快照 → `ValueError(_ERR_SNAPSHOTS_MISSING)`；最后快照日 > 请求日 → `ValueError(_ERR_SNAPSHOT_END...)`；否则返回最后快照日。
- Produces: `_persist_result(..., requested_date_to: Optional[str] = None)`；内部断言 `result["snapshots"]` 非空且末条 `trade_date == date_to`，否则 `ValueError(_ERR_PERSIST_SNAPSHOT_END)`。
- `run_meta` 新增 `requested_date_to`、`date_to_clamped`。

- [ ] Step 1: 写失败测试：`run_review_account(..., date_to=2026-09-11)` 但行情日历只到 `2026-09-10` → 返回与落库 `date_to == 2026-09-10`，`run_meta.requested_date_to == 2026-09-11`、`date_to_clamped is True`。
- [ ] Step 2: 写失败测试：直接以「末快照 09-10 / date_to 09-11」调用 `_persist_result` → `ValueError` 且不产生 run 行。
- [ ] Step 3: 实现 `resolve_persisted_date_to`；`run_review_account` 用它改写 `result["date_to"]` 并传 `requested_date_to`；verbose 时打印钳制提示。
- [ ] Step 4: `_persist_result` 加断言并通过 `children` 走原子落库。
- [ ] Step 5: 运行新测试 + `test_review_account_incremental.py` + `test_integration.py`。

### Task 3: 存量脏记录一次性修正（仅本机实库，不进代码）

- [ ] Step 1: `sqlite3` backup API 备份 `dragon.db` 到同目录带时间戳文件。
- [ ] Step 2: 事务内执行
  ```sql
  UPDATE review_account_runs
     SET date_to = (SELECT MAX(trade_date) FROM review_account_snapshots s WHERE s.run_id = review_account_runs.id)
   WHERE EXISTS (SELECT 1 FROM review_account_snapshots s WHERE s.run_id = review_account_runs.id)
     AND date_to <> (SELECT MAX(trade_date) FROM review_account_snapshots s WHERE s.run_id = review_account_runs.id);
  ```
- [ ] Step 3: 校验无剩余不一致行；用 run #8 走一次增量验证不再报错（真实行情，允许联网）。

### Task 4: 文档同步

**Files:**
- Modify: `docs/agent/trading.md`（增量回测 bullet 附近）
- Modify: `docs/solutions/2026-11-03-account-incremental-run.md`（落库/错误表）

- [ ] Step 1: 记录全量钳制语义、`run_meta.requested_date_to` / `date_to_clamped`、原子落库。
- [ ] Step 2: 错误表补充「落库前快照终点校验」对全量路径的覆盖说明。

---

## Verification

```bash
.venv/bin/python -m pytest dragon_quant/tests/test_review_account_run_integrity.py -q
.venv/bin/python -m pytest dragon_quant/tests/test_review_account_incremental.py dragon_quant/tests/test_integration.py -q
.venv/bin/python -m pytest dragon_quant/tests -q --ignore=dragon_quant/tests/e2e --ignore=dragon_quant/tests/smoke
```
