"""版本化校准：命中即 verified、未知/不同周期保持 unverified（W4a）。"""

from __future__ import annotations

from dataclasses import replace

from dragon_quant.infra import calibration


def test_matched_provenance_verifies_only_declared_periods():
    spec = calibration.PRODUCTION_CALIBRATIONS[0]
    provenance = spec.provenance
    daily = calibration.semantics_for_capture(provenance, period="daily")
    minute = calibration.semantics_for_capture(provenance, period="1min")
    five = calibration.semantics_for_capture(provenance, period="5min")
    assert daily["time_semantics"] == calibration.VERIFIED
    assert daily["unit_semantics"] == calibration.VERIFIED
    assert daily["content_semantics"] == calibration.VERIFIED
    assert minute["unit_semantics"] == calibration.VERIFIED
    # 1 分/5 分、竞价、quote 不共用同一校准结论。
    assert five["unit_semantics"] == calibration.UNVERIFIED
    assert five["calibration_id"] == ""


def test_unknown_version_or_converter_never_flips_verified():
    spec = calibration.PRODUCTION_CALIBRATIONS[0]
    wrong_version = replace(spec.provenance, install_version="0.0.0")
    wrong_time = replace(spec.provenance, bar_time="end")
    tampered = replace(
        spec.provenance,
        converter_sha256=tuple(
            (rel, "0" * 64) for rel, _sha in spec.provenance.converter_sha256))
    for provenance in (wrong_version, wrong_time, tampered, None):
        result = calibration.semantics_for_capture(provenance, period="daily")
        assert result["time_semantics"] == calibration.UNVERIFIED
        assert result["calibration_id"] == ""


def test_manifest_records_identity_and_matched_specs():
    spec = calibration.PRODUCTION_CALIBRATIONS[0]
    manifest = calibration.calibration_manifest(spec.provenance)
    assert manifest["verified"] is True
    assert spec.calibration_id in manifest["matched_specs"]
    assert manifest["provenance"]["protocol"] == "mac/0x122e"
    unknown = calibration.calibration_manifest(None)
    assert unknown["verified"] is False
    assert unknown["provenance"] is None


def test_current_install_is_either_unknown_or_a_matched_spec():
    provenance = calibration.current_tdx_provenance()
    if provenance is None:
        return
    # 当前环境若解析器身份变化，只能回到 unverified，不能错误命中。
    spec = calibration.resolve_spec(provenance, period="daily")
    assert spec is None or spec.provenance == provenance


def test_capture_collector_uses_provider_provenance():
    """采集器只信任 provider 显式身份；未知 provider 保持 unverified。"""
    from dragon_quant.data_capture.collectors.support import _capture_semantics

    class Unknown:
        pass

    class Known:
        def capture_provenance(self):
            return calibration.PRODUCTION_CALIBRATIONS[0].provenance

    assert _capture_semantics(Unknown(), "daily")["unit_semantics"] == \
        calibration.UNVERIFIED
    assert _capture_semantics(Known(), "daily")["unit_semantics"] == \
        calibration.VERIFIED
