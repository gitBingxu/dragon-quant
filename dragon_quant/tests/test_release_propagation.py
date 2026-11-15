"""Bounded registry-propagation waiting: PyPI/npm adapters and CLI resume.

These tests never publish and never wait for real time: the shared waiter takes
an injected monotonic clock and sleep, and PyPI reads a local HTTP server whose
responses can lag like a CDN.  They pin the contract that motivated the rework —
a successful upload that is briefly invisible must be *waited for*, never
mistaken for a missing artifact and re-uploaded.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dragon_quant.tests.test_release_publish import (
    ReleaseFixture,
    _pypi_args,
    _write_executable,
)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from release import consistency, publish_npm, publish_pypi  # noqa: E402
from release.common import ProgressReporter, ReleaseError, Runner  # noqa: E402

VERSION = "1.2.3"
WHEEL = f"dragon_quant-{VERSION}-py3-none-any.whl"
SDIST = f"dragon_quant-{VERSION}.tar.gz"
WHEEL_SHA = "a" * 64
SDIST_SHA = "b" * 64
EXPECTED = {WHEEL: WHEEL_SHA, SDIST: SDIST_SHA}


class FakeClock:
    """Advance a fake monotonic clock on every injected sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _policy(*, budget: float = 120.0, interval: float = 15.0,
            clock: FakeClock | None = None) -> tuple[consistency.WaitPolicy, FakeClock]:
    clock = clock or FakeClock()
    return (
        consistency.WaitPolicy(budget=budget, interval=interval,
                               clock=clock, sleep=clock.sleep),
        clock,
    )


def _reporter() -> ProgressReporter:
    return ProgressReporter(io.StringIO(), heartbeat_interval=0)


def _entry_doc(entries: list[dict], version: str = VERSION) -> str:
    return json.dumps({"info": {"version": version}, "urls": entries})


def _entry(name: str, digest: str, yanked: bool = False) -> dict:
    return {"filename": name, "digests": {"sha256": digest}, "yanked": yanked}


# ─── PyPI: local HTTP server with scripted propagation ────────────────────


class _ScriptedHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        script = self.server.script
        script["requests"].append({"path": self.path,
                                   "headers": dict(self.headers)})
        index = script["index"]
        responses = script["responses"]
        item = responses[index] if index < len(responses) else responses[-1]
        script["index"] = index + 1
        code, body = item
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        return


@contextmanager
def scripted_pypi(responses: list[tuple[int, str]]):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    server.script = {"responses": responses, "index": 0, "requests": []}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield server, f"http://127.0.0.1:{port}/pypi/dragon-quant/{{version}}/json"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_pypi_settle_waits_absent_then_partial_then_complete(monkeypatch):
    responses = [
        (404, "<html>not found</html>"),
        (200, _entry_doc([_entry(WHEEL, WHEEL_SHA)])),
        (200, _entry_doc([_entry(WHEEL, WHEEL_SHA), _entry(SDIST, SDIST_SHA)])),
    ]
    with scripted_pypi(responses) as (server, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        policy, clock = _policy()
        reporter = _reporter()
        result = publish_pypi.settle(
            VERSION, EXPECTED, progress=reporter, policy=policy)
        requests = server.script["requests"]

    assert result == {"artifacts": EXPECTED, "installable": True}
    # Three bounded attempts: absent, partial, complete — never a re-upload.
    assert len(requests) == 3
    assert all(req["path"] == f"/pypi/dragon-quant/{VERSION}/json"
               for req in requests)
    assert requests[0]["path"] == f"/pypi/dragon-quant/{VERSION}/json"
    # Explicit cache headers + a real UA, but no claim that they bypass the CDN.
    first = requests[0]["headers"]
    assert first.get("Cache-Control") == "no-cache"
    assert first.get("User-Agent", "").startswith("dragon-quant-release")
    log = reporter.stream.getvalue()
    assert "等待PyPI" in log and "剩余预算" in log and "第 2 次" in log
    assert clock.now == 30.0


def test_pypi_settle_conflicting_hash_fails_immediately(monkeypatch):
    responses = [(200, _entry_doc([_entry(WHEEL, "deadbeef"),
                                   _entry(SDIST, SDIST_SHA)]))]
    with scripted_pypi(responses) as (server, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        policy, clock = _policy()
        with pytest.raises(ReleaseError) as excinfo:
            publish_pypi.settle(VERSION, EXPECTED, policy=policy)

    assert "哈希不一致" in str(excinfo.value)
    assert len(server.script["requests"]) == 1
    assert clock.now == 0.0, "a real conflict must not be waited on"


def test_pypi_settle_budget_exhaustion_keeps_resumable(monkeypatch):
    with scripted_pypi([(404, "gone")]) as (server, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        policy, clock = _policy(budget=30.0, interval=15.0)
        with pytest.raises(consistency.PropagationExhausted) as excinfo:
            publish_pypi.settle(VERSION, EXPECTED, policy=policy)
        attempts = len(server.script["requests"])

    # Probes at t=0 and t=15; at t=30 the deadline fails without another query.
    assert attempts == 2
    message = str(excinfo.value)
    assert "--resume" in message and "传播" in message
    assert clock.now == 30.0


def test_pypi_transient_5xx_is_retried(monkeypatch):
    responses = [(503, "busy"), (200, _entry_doc(
        [_entry(WHEEL, WHEEL_SHA), _entry(SDIST, SDIST_SHA)]))]
    with scripted_pypi(responses) as (server, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        policy, _ = _policy()
        result = publish_pypi.settle(VERSION, EXPECTED, policy=policy)
        assert len(server.script["requests"]) == 2
    assert result["installable"] is True


def test_pypi_auth_error_is_terminal(monkeypatch):
    with scripted_pypi([(401, "denied")]) as (server, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        policy, clock = _policy()
        with pytest.raises(ReleaseError) as excinfo:
            publish_pypi.settle(VERSION, EXPECTED, policy=policy)
        assert len(server.script["requests"]) == 1
    assert "HTTP 401" in str(excinfo.value)
    assert clock.now == 0.0


def test_pypi_malformed_json_is_terminal(monkeypatch):
    with scripted_pypi([(200, "this is not json")]) as (server, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        policy, _ = _policy()
        with pytest.raises(ReleaseError) as excinfo:
            publish_pypi.settle(VERSION, EXPECTED, policy=policy)
        assert len(server.script["requests"]) == 1
    assert "不是合法 JSON" in str(excinfo.value)


def test_pypi_present_version_without_artifacts_is_not_absent(monkeypatch):
    document = json.dumps({"releases": {VERSION: []}})
    with scripted_pypi([(200, document)]) as (_srv, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        with pytest.raises(ReleaseError) as excinfo:
            publish_pypi.remote_artifacts(VERSION, stage="preflight")
        assert publish_pypi.version_exists(VERSION, stage="preflight") is False
    assert "没有可核验工件" in str(excinfo.value)


def test_pypi_project_level_fixture_shape_still_supported(monkeypatch):
    document = json.dumps({"releases": {VERSION: [
        _entry(WHEEL, WHEEL_SHA), _entry(SDIST, SDIST_SHA)]}})
    with scripted_pypi([(200, document)]) as (_srv, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        policy, _ = _policy()
        result = publish_pypi.settle(VERSION, EXPECTED, policy=policy)
    assert result["installable"] is True


def test_wait_is_interruptible(monkeypatch):
    with scripted_pypi([(404, "gone")]) as (_srv, url):
        monkeypatch.setenv("DQ_RELEASE_PYPI_URL", url)
        clock = FakeClock()

        def interrupt(_seconds: float) -> None:
            raise KeyboardInterrupt

        policy = consistency.WaitPolicy(budget=120.0, interval=15.0,
                                       clock=clock, sleep=interrupt)
        with pytest.raises(KeyboardInterrupt):
            publish_pypi.settle(VERSION, EXPECTED, policy=policy)


# ─── progress task: covers the probes, not just the waits ────────────────


def _alive_progress_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate()
            if thread.name == "dq-release-progress"]


class _HeartbeatStream(io.StringIO):
    """StringIO that signals the first heartbeat written to it."""

    def __init__(self) -> None:
        super().__init__()
        self.heartbeat = threading.Event()

    def write(self, text: str) -> int:
        written = super().write(text)
        if "仍在运行" in text:
            self.heartbeat.set()
        return written


def test_settle_task_starts_and_heartbeats_before_slow_probe_returns():
    stream = _HeartbeatStream()
    reporter = ProgressReporter(stream, heartbeat_interval=0.01)
    policy, clock = _policy()
    returned = threading.Event()

    def slow_probe(_remaining: float) -> str:
        # The probe blocks until the heartbeat thread has written a line: the
        # settle context must be visible *while* a slow query runs, not only
        # between attempts.
        assert stream.heartbeat.wait(5.0), "no heartbeat while the probe blocked"
        returned.set()
        return "settled"

    result = consistency.wait_for_settled(
        slow_probe, stage="upload_pypi", subject=f"PyPI {VERSION} 工件",
        policy=policy, progress=reporter)

    text = stream.getvalue()
    assert result == "settled" and returned.is_set()
    assert f"▶ 等待PyPI {VERSION} 工件传播 开始" in text
    assert "仍在运行（已耗时" in text
    assert text.strip().splitlines()[-1].startswith("✅")
    assert clock.now == 0.0, "the happy path must not sleep"
    assert _alive_progress_threads() == []


def test_settle_task_stops_and_joins_on_failure_and_interrupt():
    failure_stream = _HeartbeatStream()
    failure_reporter = ProgressReporter(failure_stream, heartbeat_interval=0.01)
    policy, _ = _policy()

    def failing(_remaining: float) -> None:
        assert failure_stream.heartbeat.wait(5.0)
        raise ReleaseError("upload_pypi", "boom")

    with pytest.raises(ReleaseError):
        consistency.wait_for_settled(
            failing, stage="upload_pypi", subject="PyPI 工件", policy=policy,
            progress=failure_reporter)
    assert "❌ 等待PyPI 工件传播 失败" in failure_stream.getvalue()
    assert _alive_progress_threads() == []

    interrupt_stream = _HeartbeatStream()
    interrupt_reporter = ProgressReporter(interrupt_stream, heartbeat_interval=0.01)

    def interrupted(_remaining: float) -> None:
        assert interrupt_stream.heartbeat.wait(5.0)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        consistency.wait_for_settled(
            interrupted, stage="upload_pypi", subject="PyPI 工件",
            policy=policy, progress=interrupt_reporter)
    assert _alive_progress_threads() == []


def test_settle_without_reporter_creates_no_thread():
    policy, _ = _policy()

    def probe(_remaining: float) -> str:
        return "ok"

    assert consistency.wait_for_settled(
        probe, stage="upload_pypi", subject="PyPI 工件",
        policy=policy) == "ok"
    assert _alive_progress_threads() == []


# ─── policy validation and the deadline boundary ─────────────────────────


@pytest.mark.parametrize("field,value", [
    ("budget", float("inf")),
    ("budget", float("nan")),
    ("budget", 0.0),
    ("budget", -5.0),
    ("interval", float("inf")),
    ("interval", float("nan")),
    ("interval", 0.0),
    ("interval", -1.0),
])
def test_wait_policy_rejects_non_finite_or_non_positive(field, value):
    with pytest.raises(ValueError):
        consistency.WaitPolicy(**{field: value})


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "0", "-3", "abc", ""])
def test_default_policy_falls_back_for_invalid_env(monkeypatch, raw):
    monkeypatch.setenv(consistency.BUDGET_ENV, raw)
    monkeypatch.setenv(consistency.INTERVAL_ENV, raw)
    policy = consistency.default_policy()
    assert policy.budget == consistency.PROPAGATION_BUDGET
    assert policy.interval == consistency.PROPAGATION_INTERVAL


def test_default_policy_honours_finite_positive_env(monkeypatch):
    monkeypatch.setenv(consistency.BUDGET_ENV, "42.5")
    monkeypatch.setenv(consistency.INTERVAL_ENV, "2")
    policy = consistency.default_policy()
    assert policy.budget == 42.5 and policy.interval == 2.0


def test_deadline_is_checked_before_another_probe():
    clock = FakeClock()
    seen: list[float] = []

    def probe(remaining: float) -> None:
        seen.append(remaining)
        raise consistency.PendingVisibility("upload_pypi", "not yet")

    policy = consistency.WaitPolicy(budget=30.0, interval=15.0,
                                   clock=clock, sleep=clock.sleep)
    with pytest.raises(consistency.PropagationExhausted):
        consistency.wait_for_settled(probe, stage="upload_pypi",
                                     subject="PyPI 工件", policy=policy)
    # Probes at t=0 and t=15 with exactly the remaining budget; the t=30
    # deadline fails without a third query (no zero/negative-timeout call).
    assert seen == [30.0, 15.0]
    assert clock.now == 30.0


def test_sleep_never_overshoots_the_budget():
    clock = FakeClock()
    probes = 0

    def probe(_remaining: float) -> None:
        nonlocal probes
        probes += 1
        raise consistency.PendingVisibility("upload_pypi", "not yet")

    policy = consistency.WaitPolicy(budget=10.0, interval=100.0,
                                   clock=clock, sleep=clock.sleep)
    with pytest.raises(consistency.PropagationExhausted):
        consistency.wait_for_settled(probe, stage="upload_pypi",
                                     subject="PyPI 工件", policy=policy)
    assert probes == 1
    assert clock.now == 10.0



# ─── npm: fake CLI with a scripted response queue ─────────────────────────

FAKE_NPM_SCRIPT = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

path = Path(os.environ["FAKE_NPM_QUEUE"])
data = json.loads(path.read_text())
index = data["index"]
responses = data["responses"]
item = responses[index] if index < len(responses) else responses[-1]
data["index"] = index + 1
path.write_text(json.dumps(data))
if item.get("stderr"):
    sys.stderr.write(item["stderr"])
if item.get("stdout"):
    sys.stdout.write(item["stdout"])
raise SystemExit(item.get("code", 0))
"""


def _npm_runner(tmp_path: Path, responses: list[dict]) -> tuple[Runner, Path]:
    bin_dir = tmp_path / "fake-npm-bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "npm", FAKE_NPM_SCRIPT)
    queue = tmp_path / "npm-queue.json"
    queue.write_text(json.dumps({"responses": responses, "index": 0}))
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_NPM_QUEUE": str(queue),
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmpdir"),
    }
    (tmp_path / "home").mkdir(exist_ok=True)
    (tmp_path / "tmpdir").mkdir(exist_ok=True)
    return Runner(cwd=tmp_path, env=env), queue


def test_npm_waits_e404_and_missing_integrity_then_matches(tmp_path):
    responses = [
        {"code": 1, "stderr": "npm error code E404\n404 Not Found"},
        {"code": 0, "stdout": "{}"},
        {"code": 0, "stdout": json.dumps({"integrity": "sha512-ok"})},
    ]
    runner, queue = _npm_runner(tmp_path, responses)
    policy, clock = _policy()
    assert publish_npm.reconcile("1.0.3", "sha512-ok", runner, wait=True,
                                 policy=policy) is True
    assert json.loads(queue.read_text())["index"] == 3
    assert clock.now == 30.0


def test_npm_integrity_mismatch_fails_immediately(tmp_path):
    responses = [{"code": 0, "stdout": json.dumps({"integrity": "sha512-other"})}]
    runner, queue = _npm_runner(tmp_path, responses)
    policy, clock = _policy()
    with pytest.raises(ReleaseError) as excinfo:
        publish_npm.reconcile("1.0.3", "sha512-ok", runner, wait=True,
                              policy=policy)
    assert "integrity 不一致" in str(excinfo.value)
    assert json.loads(queue.read_text())["index"] == 1
    assert clock.now == 0.0


def test_npm_auth_error_is_not_waited(tmp_path):
    responses = [{"code": 1, "stderr": "npm error code E401\nunauthorized"}]
    runner, queue = _npm_runner(tmp_path, responses)
    policy, clock = _policy()
    with pytest.raises(ReleaseError) as excinfo:
        publish_npm.reconcile("1.0.3", "sha512-ok", runner, wait=True,
                              policy=policy)
    assert "鉴权失败" in str(excinfo.value)
    assert json.loads(queue.read_text())["index"] == 1
    assert clock.now == 0.0


def test_npm_one_shot_absent_is_not_an_error(tmp_path):
    runner, _ = _npm_runner(
        tmp_path, [{"code": 1, "stderr": "npm error code E404"}])
    assert publish_npm.reconcile("1.0.3", "sha512-ok", runner) is False


# ─── CLI: lagging "CDN" + receipt means resume never re-uploads ───────────

LAG = 10_000


class _CdnHandler(BaseHTTPRequestHandler):
    """Serve a version-level document from the origin JSON, with a lag gate."""

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler API
        script = self.server.script
        script["requests"] += 1
        parts = self.path.strip("/").split("/")
        version = parts[-2] if len(parts) >= 2 else ""
        lag_file = Path(script["lag_file"])
        lag = int(lag_file.read_text() or "0") if lag_file.exists() else 0
        if lag > 0:
            lag_file.write_text(str(lag - 1))
            self._send(404, "<html>not found</html>")
            return
        origin = Path(script["origin"])
        document = json.loads(origin.read_text()) if origin.exists() else {}
        entries = (document.get("releases") or {}).get(version)
        if entries is None:
            self._send(404, "<html>not found</html>")
            return
        self._send(200, json.dumps({"info": {"version": version}, "urls": entries}))

    def _send(self, code: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        return


@contextmanager
def cdn(origin: Path, lag_file: Path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CdnHandler)
    server.script = {"origin": str(origin), "lag_file": str(lag_file),
                     "requests": 0}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        url = (f"http://127.0.0.1:{port}/pypi/dragon-quant/{{version}}/json")
        yield server, url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _upload_calls(fixture: ReleaseFixture) -> list[dict]:
    return [call for call in fixture.py_calls()
            if call["argv"][:3] == ["-m", "twine", "upload"]]


def test_resume_waits_out_pypi_lag_without_reuploading(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    lag_file = tmp_path / "lag.txt"
    with cdn(fixture.pypi_json_path, lag_file) as (server, url):
        env = fixture.env(extra={
            "TWINE_PASSWORD": "x",
            "DQ_RELEASE_PYPI_URL": url,
            "FAKE_PY_PYPI_PATH": str(fixture.pypi_json_path),
            "FAKE_PY_LAG_PATH": str(lag_file),
            "FAKE_PY_LAG": str(LAG),
            "DQ_RELEASE_PROPAGATION_BUDGET": "0.4",
            "DQ_RELEASE_PROPAGATION_INTERVAL": "0.05",
        })
        first = fixture.run(*_pypi_args(), "--yes", env=env)
        assert first.returncode == 1, first.stderr
        assert "等待PyPI" in first.stderr
        # The whole settle context has a start line on stderr, never stdout.
        assert "▶ 等待PyPI 1.2.3 工件传播 开始" in first.stderr
        assert "等待PyPI" not in first.stdout and "仍在运行" not in first.stdout
        assert "--resume" in first.stderr
        uploads = len(_upload_calls(fixture))
        assert uploads == 1
        state = fixture.state()
        assert state["steps"]["upload_pypi"].get("done") is not True
        assert state["steps"]["upload_pypi"]["result"].get("accepted") is True

        # The upload landed at the origin; drop the CDN lag and resume.
        lag_file.write_text("0")
        second = fixture.run("--resume", "--yes", env=fixture.env(extra={
            "DQ_RELEASE_PYPI_URL": url,
            "DQ_RELEASE_PROPAGATION_BUDGET": "5.0",
        }))
    assert second.returncode == 0, second.stderr
    assert len(_upload_calls(fixture)) == uploads, "resume must not re-upload"
    assert fixture.state()["steps"]["upload_pypi"]["done"] is True
    assert server.script["requests"] > 0


def test_resume_waits_out_npm_lag_without_republishing(tmp_path):
    from dragon_quant.tests.test_unified_release import _npm_args

    fixture = ReleaseFixture(tmp_path, pypi_current="1.0.0")
    fixture.set_pypi_releases({"1.0.0": [{
        "filename": "dragon_quant-1.0.0-py3-none-any.whl",
        "digests": {"sha256": "0" * 64}}]})
    lag_env = {
        "NPM_TOKEN": "npm-secret",
        "FAKE_NPM_LAG": str(LAG),
        "DQ_RELEASE_PROPAGATION_BUDGET": "0.4",
        "DQ_RELEASE_PROPAGATION_INTERVAL": "0.05",
    }
    first = fixture.run(*_npm_args(), "--yes", env=fixture.env(extra=lag_env))
    assert first.returncode == 1, first.stderr
    publishes = [call for call in fixture.npm_calls() if call[:1] == ["publish"]]
    assert len(publishes) == 1
    assert "等待npm" in first.stderr
    assert "▶ 等待npm dragon-quant-skills@1.0.3 工件传播 开始" in first.stderr
    assert "等待npm" not in first.stdout
    state = fixture.state()
    assert state["steps"]["upload_npm"]["result"].get("accepted") is True

    npm_state = json.loads(fixture.npm_state.read_text(encoding="utf-8"))
    npm_state["lag"] = {}
    fixture.npm_state.write_text(json.dumps(npm_state), encoding="utf-8")

    second = fixture.run("--resume", "--yes", env=fixture.env(extra={
        "NPM_TOKEN": "npm-secret"}))
    assert second.returncode == 0, second.stderr
    publishes = [call for call in fixture.npm_calls() if call[:1] == ["publish"]]
    assert len(publishes) == 1, "resume must not publish again"


def test_dry_run_makes_zero_registry_requests(tmp_path):
    fixture = ReleaseFixture(tmp_path)
    with cdn(fixture.pypi_json_path, tmp_path / "lag.txt") as (server, url):
        result = fixture.run("--packages", "both", "--pypi-version", "1.2.3",
                             "--npm-version", "1.0.3", "--dry-run",
                             env=fixture.env(extra={"DQ_RELEASE_PYPI_URL": url}))
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout[result.stdout.index("{"):])
    assert summary["network_calls"] == 0
    assert server.script["requests"] == 0
