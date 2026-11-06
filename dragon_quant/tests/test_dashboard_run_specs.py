"""dashboard 命令运行注册表：参数校验与 argv 组装（零 I/O）。"""

from __future__ import annotations

import pytest

from web_ui.backend.run_specs import (
    ParamError,
    UnknownJob,
    build_argv,
    get_spec,
    spec_payload,
    validate_params,
)


def test_spec_payload_exposes_quick_labels_and_window() -> None:
    scan = spec_payload("scan")
    assert scan["quick_label"] == "龙头扫描"
    assert scan["custom_label"] == "自定义扫描"
    assert scan["window"] == {"start": "15:00", "end": "23:55"}

    prep = spec_payload("prep")
    assert prep["quick_label"] == "今日复盘"
    assert prep["window"] == {"start": "21:00", "end": "23:55"}

    auction = spec_payload("auction")
    assert auction["quick_label"] == "竞价强度判断"
    assert auction["window"] == {"start": "09:25", "end": "23:55"}


def test_spec_payload_carries_form_constraints() -> None:
    params = {item["name"]: item for item in spec_payload("scan")["params"]}
    assert params["top"]["kind"] == "int"
    assert params["top"]["default"] == 5
    assert (params["top"]["minimum"], params["top"]["maximum"]) == (1, 100)
    assert params["workers"]["default"] == 2

    auction = {item["name"]: item for item in spec_payload("auction")["params"]}
    assert auction["pool"]["choices"][0] == {
        "value": "prep", "label": "盘后复盘推荐（默认）"}
    assert auction["days"]["depends_on"] == {"param": "pool", "value": "legacy"}
    assert auction["force"]["kind"] == "bool"


def test_unknown_job_is_rejected() -> None:
    with pytest.raises(UnknownJob):
        get_spec("review")


def test_scan_argv_uses_quick_defaults_and_registered_fields() -> None:
    params = validate_params("scan", {})
    assert params == {"top": 5, "candidates": 5, "workers": 2}
    argv = build_argv("scan", params, trade_date="2026-11-06")
    assert argv[0].endswith("python") or "python" in argv[0]
    assert argv[1:] == [
        "-m", "dragon_quant", "scan",
        "--top", "5", "--candidates", "5", "--workers", "2",
    ]


def test_prep_argv_pins_today_and_maps_modes() -> None:
    incremental = validate_params("prep", {})
    assert build_argv("prep", incremental, trade_date="2026-11-06")[1:] == [
        "-m", "dragon_quant", "prep", "--date", "20261106"]

    forced = validate_params("prep", {"mode": "force"})
    assert build_argv("prep", forced, trade_date="2026-11-06")[-1] == "--force"

    recompute = validate_params("prep", {"mode": "recompute"})
    assert build_argv("prep", recompute, trade_date="2026-11-06")[-1] == "--recompute"


def test_auction_argv_only_passes_days_for_legacy_and_force_when_set() -> None:
    prep_pool = validate_params("auction", {})
    assert build_argv("auction", prep_pool, trade_date="2026-11-06")[1:] == [
        "-m", "dragon_quant", "auction", "--pool", "prep"]

    legacy = validate_params("auction", {"pool": "legacy", "days": 3})
    assert build_argv("auction", legacy, trade_date="2026-11-06")[1:] == [
        "-m", "dragon_quant", "auction", "--pool", "legacy", "--days", "3"]

    forced = validate_params("auction", {"force": True})
    assert build_argv("auction", forced, trade_date="2026-11-06")[-1] == "--force"


@pytest.mark.parametrize("payload, field", [
    ({"top": 0}, "top"),
    ({"top": 101}, "top"),
    ({"candidates": "abc"}, "candidates"),
    ({"workers": True}, "workers"),
    ({"workers": 2.5}, "workers"),
    ({"force": True}, "force"),
])
def test_scan_rejects_invalid_params(payload: dict, field: str) -> None:
    with pytest.raises(ParamError) as excinfo:
        validate_params("scan", payload)
    assert excinfo.value.field == field


def test_unknown_param_and_bad_enum_are_rejected() -> None:
    with pytest.raises(ParamError) as unknown:
        validate_params("prep", {"date": "20261106"})
    assert "未知参数" in unknown.value.message

    with pytest.raises(ParamError) as mode:
        validate_params("prep", {"mode": "auto"})
    assert "运行模式" in mode.value.message

    with pytest.raises(ParamError) as pool:
        validate_params("auction", {"pool": "daily"})
    assert "候选池来源" in pool.value.message


def test_numeric_strings_and_bool_strings_are_accepted() -> None:
    assert validate_params("scan", {"top": "12", "workers": "3"})["top"] == 12
    assert validate_params("auction", {"force": "true"})["force"] is True
    assert validate_params("auction", {"force": "0"})["force"] is False
