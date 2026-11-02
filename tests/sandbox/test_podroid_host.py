from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace

from yoreni.sandbox import setup as sandbox_setup_api
from yoreni.sandbox.config import SandboxConfig
from yoreni.sandbox.podroid_backend import HttpVmControl, host_control
from yoreni.sandbox.setup import SandboxSetup

URL = "http://127.0.0.1:8766/podroid"


def test_only_a_podroid_host_with_a_control_url_is_asked() -> None:
    assert host_control(SandboxConfig(backend="boxlite")) is None
    # A desktop driving Podroid over adb has no loopback endpoint to ask.
    assert host_control(SandboxConfig(backend="podroid")) is None
    assert host_control(
        SandboxConfig(backend="podroid", podroid_control_url=URL)
    ) is not None


def test_host_mode_decides_whether_first_run_offers_the_app() -> None:
    gateway = SimpleNamespace(
        config=SandboxConfig(backend="podroid", podroid_control_url=URL)
    )
    original = sandbox_setup_api.get_sandbox_gateway
    sandbox_setup_api.get_sandbox_gateway = lambda: gateway
    try:
        assert SandboxSetup().host_mode() == "podroid"
    finally:
        sandbox_setup_api.get_sandbox_gateway = original

    gateway.config = SandboxConfig(backend="boxlite")
    sandbox_setup_api.get_sandbox_gateway = lambda: gateway
    try:
        assert SandboxSetup().host_mode() == "desktop"
    finally:
        sandbox_setup_api.get_sandbox_gateway = original


def test_status_is_read_and_the_download_is_opened() -> None:
    seen: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            seen.append(("GET", self.path))
            body = json.dumps(
                {"installed": True, "vm_running": False, "version": "1.2.9"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            seen.append(("POST", self.path))
            body = b"ok opened"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        control = HttpVmControl(f"http://127.0.0.1:{server.server_address[1]}/podroid")
        assert asyncio.run(control.status()) == {
            "installed": True,
            "vm_running": False,
            "version": "1.2.9",
        }
        asyncio.run(control.install())
        asyncio.run(control("start"))
    finally:
        server.shutdown()
        server.server_close()

    assert seen == [
        ("GET", "/podroid/status"),
        ("POST", "/podroid/install"),
        ("POST", "/podroid/start"),
    ]
