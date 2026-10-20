"""The shared loopback helper owns transport, shutdown and bounded lifetime."""

import contextlib
import http.client
import json
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from scripts.common import ROOT

HELPER = (
    ROOT
    / "collection_modules/tests/integration/targets/setup_loopback/files/server.py.j2"
)
DRIVER = """
import json
import sys
from pathlib import Path
from server import serve

state = Path(sys.argv[1])

def authorized(request):
    return request.headers.get("Authorization") == "fixture-access"

def route(request):
    size = int(request.headers.get("Content-Length", "0"))
    body = request.rfile.read(size).decode()
    payload = {
        "method": request.command,
        "path": request.path,
        "body": body,
        "address": request.connection.getsockname()[0],
    }
    request.reply(202, json.dumps(payload).encode(), {"X-Fixture": "route"})

serve(
    state, float(sys.argv[2]), route, authorized,
    methods=tuple(sys.argv[3].split(",")),
    shutdown_path="/fixture/stop",
    shutdown_payload={"stopped": True},
    denied_payload={"stopped": False},
)
"""


@contextlib.contextmanager
def server(
    state: Path,
    *,
    lifetime: float = 120,
    methods: str = "GET,POST,PUT,PATCH,DELETE",
) -> Iterator[tuple[subprocess.Popen[str], int]]:
    source = "".join(
        line
        for line in HELPER.read_text().splitlines(keepends=True)
        if not line.startswith("# SPDX")
    )
    (state / "server.py").write_text(source, encoding="utf-8")
    driver = state / "routes.py"
    driver.write_text(DRIVER, encoding="utf-8")
    with (state / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(driver), str(state), str(lifetime), methods],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + 30
            port_file = state / "port"
            while not port_file.is_file() and process.poll() is None:
                assert time.monotonic() < deadline, "No loopback port was published"
                time.sleep(0.01)
            assert port_file.is_file(), (state / "server.log").read_text()
            port = int(port_file.read_text())
            assert 0 < port < 65536
            assert not (state / "port.tmp").exists()
            yield process, port
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def request(
    port: int,
    method: str,
    path: str,
    *,
    body: str = "",
    authorized: bool = False,
) -> tuple[int, bytes, dict[str, str]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    headers = {"Authorization": "fixture-access"} if authorized else {}
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def test_dispatch_and_authenticated_shutdown(tmp_path: Path) -> None:
    with server(tmp_path) as (process, port):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            status, body, headers = request(
                port, method, "/product?test=1", body="data"
            )
            assert status == 202 and headers["X-Fixture"] == "route"
            assert int(headers["Content-Length"]) == len(body)
            payload: dict[str, Any] = json.loads(body)
            assert payload == {
                "method": method,
                "path": "/product?test=1",
                "body": "data",
                "address": "127.0.0.1",
            }
        status, body, _headers = request(port, "POST", "/fixture/stop")
        assert status == 401 and json.loads(body) == {"stopped": False}
        assert process.poll() is None
        assert request(port, "GET", "/still-running")[0] == 202
        status, body, _headers = request(port, "POST", "/fixture/stop", authorized=True)
        assert status == 200 and json.loads(body) == {"stopped": True}
        assert process.wait(timeout=30) == 0


def test_configured_methods_are_respected(tmp_path: Path) -> None:
    with server(tmp_path, methods="GET,POST") as (process, port):
        assert request(port, "DELETE", "/product")[0] == 501
        assert request(port, "GET", "/product")[0] == 202
        assert request(port, "POST", "/fixture/stop", authorized=True)[0] == 200
        assert process.wait(timeout=30) == 0


def test_lifetime_stops_the_listener(tmp_path: Path) -> None:
    with server(tmp_path, lifetime=0.1) as (process, _port):
        assert process.wait(timeout=30) == 0
