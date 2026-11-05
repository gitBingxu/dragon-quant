"""Long-running HTTP server helper for the Web E2E cases.

The M0 runner only covers commands that terminate.  A server never exits on its
own, so this module adds exactly two things on top of the same mandatory
contract:

* the identical-environment preflight is still executed *before* the server is
  started, with ``allow_localhost`` explicitly enabled (the preflight still
  proves external DNS/sockets are blocked);
* the child is started in its own process group and torn down with SIGTERM /
  SIGKILL, and the helper asserts the TCP port is released afterwards, on every
  path (success, assertion failure, or startup failure).

Ports are OS-assigned: the command runs with ``--port 0`` and the real port is
parsed from the server's own startup line (``http://localhost:<port>``) while
the child is still alive.  The child already runs with ``PYTHONUNBUFFERED=1``
from the sandbox environment, so the line is flushed immediately; the harness
reads the pipes in non-blocking mode (``os.set_blocking`` + ``os.read``) and
never uses a helper thread.

The parent test process is the only HTTP client and connects to ``127.0.0.1``.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from .runner import (
    REPO_ROOT,
    E2EHarnessError,
    _build_env,
    _clear_artifacts,
    _validate_command_args,
    _verify_marker,
    preflight,
)
from .sandbox import EnvSandbox

LOCAL_HOST = "127.0.0.1"
_READY_POLL_S = 0.05
_EXIT_CAPTURE_CHARS = 4000
_READ_CHUNK = 65536
_URL_RE = re.compile(r"http://localhost:(\d+)")


class WebServerError(E2EHarnessError):
    """The Web server did not start, or did not release its port on teardown."""


# ─── ports ──────────────────────────────────────────────────────────────────


def free_port(host: str = LOCAL_HOST) -> int:
    """Reserve-then-release a port in the ephemeral range.

    Binding ``port=0`` lets the OS pick a free port; closing the socket before
    the child starts leaves a small, unavoidable race that is acceptable for a
    serial offline suite.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def port_is_free(port: int, host: str = LOCAL_HOST) -> bool:
    """True when a listener could bind ``port`` (mirrors the server's flags)."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def _wait_port_free(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_is_free(port):
            return True
        time.sleep(_READY_POLL_S)
    return port_is_free(port)


# ─── HTTP ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WebResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


# ─── server process ─────────────────────────────────────────────────────────


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    if os.name == "nt":  # pragma: no cover - CI is POSIX
        process.kill()
        return
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


@dataclass
class WebServer:
    sandbox: EnvSandbox
    port: int
    command: tuple[str, ...]
    process: subprocess.Popen
    port_was_free: bool = True
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    _stopped: bool = False
    _output_collected: bool = False
    _dynamic: bool = False
    _stdout_eof: bool = False
    _stderr_eof: bool = False

    # ─── HTTP client ──────────────────────────────────────────────────────

    def request(self, method: str = "GET", path: str = "/", *,
                payload: object | None = None,
                timeout: float = 15.0) -> WebResponse:
        body: bytes | None = None
        # 浏览器优先 Accept text/html：SPA fallback 只在 HTML 请求上生效，
        # 与生产浏览器行为一致；API 路径的 JSON 响应与 Accept 无关。
        headers = {"Accept": "text/html,application/json;q=0.9"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(LOCAL_HOST, self.port, timeout=timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            response_headers = {
                key.lower(): value for key, value in response.getheaders()
            }
            return WebResponse(response.status, response_headers, raw)
        finally:
            connection.close()

    # ─── lifecycle ────────────────────────────────────────────────────────

    # ─── dynamic port discovery (``--port 0``) ────────────────────────────

    def _drain_stream(self, name: str) -> None:
        """Read whatever a non-blocking pipe currently holds into ``name``."""

        stream = self.process.stdout if name == "stdout" else self.process.stderr
        eof_attr = "_stdout_eof" if name == "stdout" else "_stderr_eof"
        if stream is None or getattr(self, eof_attr):
            return
        chunks: list[str] = []
        while True:
            try:
                chunk = os.read(stream.fileno(), _READ_CHUNK)
            except BlockingIOError:
                break
            except (OSError, ValueError):
                setattr(self, eof_attr, True)
                break
            if not chunk:
                setattr(self, eof_attr, True)
                break
            chunks.append(chunk.decode("utf-8", errors="replace"))
        if chunks:
            setattr(self, name, getattr(self, name) + "".join(chunks))

    def _drain_output(self) -> None:
        self._drain_stream("stdout")
        self._drain_stream("stderr")

    def _finish_dynamic_output(self, wait: float = 2.0) -> None:
        deadline = time.monotonic() + wait
        while not (self._stdout_eof and self._stderr_eof):
            self._drain_output()
            if self._stdout_eof and self._stderr_eof:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(_READY_POLL_S)

    def _collect_dynamic_output(self) -> None:
        self._finish_dynamic_output()
        self._output_collected = True
        self.returncode = self.process.returncode
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _await_assigned_port(self, deadline: float, timeout: float) -> None:
        while time.monotonic() < deadline:
            self._drain_output()
            match = _URL_RE.search(self.stdout)
            if match:
                self.port = int(match.group(1))
                return
            if self.process.poll() is not None:
                self._collect_output()
                raise WebServerError(
                    "Web 进程在打印监听端口前退出（--port 0, "
                    f"exit={self.returncode}）\n{self._output_tail()}"
                )
            time.sleep(_READY_POLL_S)
        self._drain_output()
        raise WebServerError(
            f"Web 进程 {timeout:.0f}s 内未打印监听端口（--port 0）\n"
            f"{self._output_tail()}"
        )

    # ─── output / readiness / teardown ────────────────────────────────────

    def _collect_output(self) -> None:
        if self._output_collected:
            return
        if self._dynamic:
            self._collect_dynamic_output()
            return
        stdout, stderr = self.process.communicate()
        self._output_collected = True
        self.stdout += stdout or ""
        self.stderr += stderr or ""
        self.returncode = self.process.returncode

    def _output_tail(self) -> str:
        return (
            f"stdout:\n{self.stdout[-_EXIT_CAPTURE_CHARS:]}\n"
            f"stderr:\n{self.stderr[-_EXIT_CAPTURE_CHARS:]}"
        )

    def _wait_ready(self, ready_path: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        if self._dynamic:
            self._await_assigned_port(deadline, timeout)
        self._await_http_ready(ready_path, deadline, timeout)

    def _await_http_ready(self, ready_path: str, deadline: float,
                          timeout: float) -> None:
        last_error: object = None
        while time.monotonic() < deadline:
            if self._dynamic:
                # Keep the non-blocking pipes drained so a chatty child can
                # never fill the OS buffer and block before it is torn down.
                self._drain_output()
            if self.process.poll() is not None:
                self._collect_output()
                raise WebServerError(
                    f"Web 进程在就绪前退出（port={self.port}, "
                    f"exit={self.returncode}）\n{self._output_tail()}"
                )
            try:
                response = self.request("GET", ready_path, timeout=2.0)
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                time.sleep(_READY_POLL_S)
                continue
            if response.status < 500:
                return
            last_error = f"HTTP {response.status}"
            time.sleep(_READY_POLL_S)
        self._drain_output()
        raise WebServerError(
            f"Web 进程 {timeout:.0f}s 内未就绪（port={self.port}, "
            f"last_error={last_error!r}）\n{self._output_tail()}"
        )

    def stop(self, *, timeout: float = 5.0) -> None:
        """Terminate the process group (idempotent) and assert the port frees."""

        if self._stopped:
            return
        self._stopped = True
        if not self._output_collected:
            if self._dynamic:
                if self.process.poll() is None:
                    _signal_group(self.process, signal.SIGTERM)
                    try:
                        self.process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        _signal_group(self.process, signal.SIGKILL)
                        self.process.wait()
                self._collect_dynamic_output()
            else:
                if self.process.poll() is None:
                    _signal_group(self.process, signal.SIGTERM)
                    try:
                        stdout, stderr = self.process.communicate(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        _signal_group(self.process, signal.SIGKILL)
                        stdout, stderr = self.process.communicate()
                else:
                    stdout, stderr = self.process.communicate()
                self._output_collected = True
                self.stdout += stdout or ""
                self.stderr += stderr or ""
                self.returncode = self.process.returncode
        _verify_marker(self.sandbox)
        # Only a port this helper observed free *before* spawn can be asserted
        # released; a deliberately pre-bound port (startup-failure case) is not
        # ours to free, and an undiscovered ``--port 0`` port is unknown.
        if self.port_was_free and self.port > 0 and not _wait_port_free(self.port, timeout):
            raise WebServerError(
                f"Web 进程已退出但端口未释放: port={self.port}\n{self._output_tail()}"
            )

    def __enter__(self) -> "WebServer":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


# ─── public entry point ─────────────────────────────────────────────────────


def start_web_server(
    command: Sequence[str],
    *,
    sandbox: EnvSandbox,
    port: int,
    ready_path: str = "/",
    timeout: float = 60.0,
    env_override: Mapping[str, object] | None = None,
) -> WebServer:
    """Preflight (localhost allowed), spawn the server, wait until it answers.

    ``port=0`` asks the OS for an ephemeral port: the real port is parsed from
    the server's startup line while the child is alive, then used for the
    readiness probe and every subsequent request.
    """

    argv = [str(item) for item in command]
    _validate_command_args(argv, sandbox)
    env = _build_env(sandbox, allow_localhost=True, env_override=env_override)
    preflight(sandbox, allow_localhost=True, env=env)
    _clear_artifacts(sandbox, "injection.json")
    dynamic = int(port) <= 0
    port_was_free = port_is_free(port)

    kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
    }
    if os.name == "nt":  # pragma: no cover - CI is POSIX
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(
        argv, env=dict(env), cwd=str(REPO_ROOT), **kwargs  # type: ignore[arg-type]
    )
    if dynamic:
        try:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    os.set_blocking(stream.fileno(), False)
        except (AttributeError, OSError, ValueError) as exc:
            _signal_group(process, signal.SIGKILL)
            process.wait()
            raise WebServerError(
                f"--port 0 动态端口发现需要 POSIX 非阻塞管道: {exc}"
            ) from exc
    server = WebServer(
        sandbox=sandbox, port=int(port), command=tuple(argv), process=process,
        port_was_free=port_was_free, _dynamic=dynamic,
    )
    try:
        server._wait_ready(ready_path, timeout)
    except BaseException:
        server.stop()
        raise
    return server


if __name__ == "__main__":  # pragma: no cover - diagnostic entry
    print("usage: import dragon_quant.tests.e2e.harness.web", file=sys.stderr)
    raise SystemExit(2)
