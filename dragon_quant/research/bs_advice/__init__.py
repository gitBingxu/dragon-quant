"""BS 只读建议与账户入口（W4a）。

公共 API（CLI/研究/后续 Web 共用）：

- :func:`load_account_file` / :func:`load_policy_file`：显式本机路径读取 + 校验；
- :func:`build_advice`：账户政策只读建议（事前母集 + 证据 + W2 政策核）；
- :func:`run_bs_review`：review-account BS 分支的严格 PIT 盘中重放/持久化；
- :func:`read_run`：有界只读账本查询。

不联网、不下单；写只发生在显式 review 运行（studies/preview 显式 persist=False）。
"""

from __future__ import annotations

from .account_file import (
    ACCOUNT_FILE_SCHEMA_VERSION, AccountFileError, AccountSnapshot,
    load_account_file, parse_account_file,
)
from .advice import (
    BsAdviceError, build_advice, build_advice_from_files, origin_batch_id,
)
from .assembly import assemble_snapshot, capabilities_from_snapshot
from .candidates import build_candidates
from .dto import Advice, Capabilities, Coverage, RunRecord
from .experiment import (
    EXPERIMENT_VERSION, BsExperimentError, build_experiment_pack,
    finish_experiment_run,
    find_bs_experiment_by_digest, list_bs_experiments, replay_bs_experiment,
    resume_bs_experiment, run_bs_experiment, run_frozen_experiment,
    save_experiment_run, show_bs_experiment,
)
from .events import (
    DEFAULT_MAX_LAG_MS, DEFAULT_PARTICIPATION, ExecutionEvidenceError,
    IntradayEvents, build_intraday_events,
)
from .market import market_from_universe
from .metrics import METRICS_VERSION, compute_metrics
from .policy import (
    LoadedPolicy, PolicyFileError, load_policy_file, load_policy_payload,
    read_json_object,
)
from .review import BsReviewError, DEFAULT_MAX_QUOTE_AGE_MS, run_bs_review
from .store import BsRunError, persist_run, read_run, run_ledger_id
from .universe import (
    ExAnteUniverse, UniverseItem, assemble_universe,
)

__all__ = [
    "ACCOUNT_FILE_SCHEMA_VERSION", "AccountFileError", "AccountSnapshot",
    "load_account_file", "parse_account_file",
    "PolicyFileError", "LoadedPolicy", "load_policy_file", "load_policy_payload",
    "read_json_object",
    "BsAdviceError", "build_advice", "build_advice_from_files", "origin_batch_id",
    "BsReviewError", "DEFAULT_MAX_QUOTE_AGE_MS", "run_bs_review",
    "BsRunError", "persist_run", "read_run", "run_ledger_id",
    "assemble_snapshot", "capabilities_from_snapshot",
    "ExAnteUniverse", "UniverseItem", "assemble_universe",
    "build_candidates", "market_from_universe",
    "DEFAULT_MAX_LAG_MS", "DEFAULT_PARTICIPATION", "ExecutionEvidenceError",
    "IntradayEvents", "build_intraday_events",
    "EXPERIMENT_VERSION", "BsExperimentError", "run_bs_experiment",
    "build_experiment_pack", "save_experiment_run",
    "finish_experiment_run", "list_bs_experiments",
    "show_bs_experiment", "find_bs_experiment_by_digest",
    "replay_bs_experiment", "resume_bs_experiment", "run_frozen_experiment",
    "METRICS_VERSION", "compute_metrics",
    "Advice", "Capabilities", "Coverage", "RunRecord",
]
