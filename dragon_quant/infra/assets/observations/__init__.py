"""assets.db 采集观测仓储稳定入口（计划/运行/观测/bar 的原子读写）。

职责分层：schema/错误 → store 连接与哈希 → plans/runs → write 原子事务 →
observations/bars 只读查询。外部继续使用
``from dragon_quant.infra.assets import observations``。
"""

from __future__ import annotations

from .bars import (
    bar_groups,
    bar_interval_states,
    query_bars,
    select_bars_asof,
)
from .observations import (
    observation_groups,
    query_observations,
    select_observations_asof,
)
from .plans import (
    count_plans,
    get_plan,
    latest_plan,
    list_plans,
    previous_plan,
    save_plan,
)
from .runs import (
    acquire_lease,
    count_runs,
    ensure_run,
    get_run,
    list_runs,
    release_lease,
    start_run,
)
from .schema import (
    CAPTURE_TABLES,
    MISSING_DATABASE,
    READY,
    SCHEMA_VERSION,
    UNINITIALIZED,
    CorruptEvidence,
    LeaseLostError,
    ObservationStoreError,
    StoreUninitialized,
)
from .store import canonical_json, content_hash, store_state
from .write import (
    append_bar,
    append_observation,
    commit_topic,
    finalize_run,
    mark_run,
)

__all__ = [
    "SCHEMA_VERSION", "CAPTURE_TABLES", "READY", "UNINITIALIZED",
    "MISSING_DATABASE", "ObservationStoreError", "StoreUninitialized",
    "CorruptEvidence", "LeaseLostError", "canonical_json", "content_hash",
    "store_state",
    "get_plan", "latest_plan", "previous_plan", "list_plans", "count_plans",
    "save_plan",
    "ensure_run", "get_run", "count_runs", "list_runs", "acquire_lease",
    "release_lease", "start_run", "finalize_run", "mark_run",
    "select_observations_asof", "query_observations", "observation_groups",
    "select_bars_asof", "query_bars", "bar_groups", "bar_interval_states",
    "commit_topic", "append_observation", "append_bar",
]
