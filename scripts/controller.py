# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Budget and exercise run-private controller and Podman Unix sockets."""

import argparse
import multiprocessing
import os
import platform
import sys
from multiprocessing.managers import BaseManager
from pathlib import Path

from scripts.common import GateError, write_json

SOCKET_LIMIT = 104
# Python 3.12/3.13 use listener-XXXXXXXX; 3.14 uses sock-XXXXXXXXXXXX.
CONTROLLER_SUFFIX = "pymp-xxxxxxxx/listener-xxxxxxxx"
# Each step of a check run has its own TMPDIR, t/<slot>, one character each.
STEP_SLOTS = "0123456789abcdefghijklmnopqrstuvwxyz"
# Generated collections' nox environments live below the step's TMPDIR, and
# Python 3.14 binds the docs-check forkserver socket in the session's own tmp
# directory. The short prefix pays for the slot directory.
NOX_ENVDIR_PREFIX = "n"
NOX_LAYOUT = f"t/x/{NOX_ENVDIR_PREFIX}xxxxxxxx/docs-check-3-14/tmp/{CONTROLLER_SUFFIX}"
# Molecule names SSH control sockets %h-%p-%r; OpenSSH binds the master socket
# under a 16-character temporary suffix first.
SSH_CONTROL_DIRECTORY = "s"
SSH_CONTROL_LAYOUT = f"{SSH_CONTROL_DIRECTORY}/127.0.0.1-65535-molecule.{'x' * 16}"


def check_socket_layout(output: Path, *layouts: str) -> None:
    """Reject layouts before any matrix work or runtime state is created."""
    output = output.resolve()
    for relative in (f"t/{CONTROLLER_SUFFIX}", "r/podman/podman.sock", *layouts):
        path = output / relative
        if not path.resolve().is_relative_to(output):
            raise GateError("Socket directory is outside this run")
        if len(os.fsencode(path)) >= SOCKET_LIMIT:
            raise GateError(
                "Use a shorter output path for private controller and Podman sockets"
            )


def controller_environment(output: Path, env: dict[str, str]) -> dict[str, str]:
    """Allocate a short temporary directory shared by controller subprocesses."""
    output = output.resolve()
    check_socket_layout(output)
    temporary = output / "t"
    temporary.mkdir(mode=0o700, parents=True, exist_ok=True)
    if temporary.stat().st_mode & 0o077:
        raise GateError("Controller temporary directory must be private to its owner")
    return {**env, "TMPDIR": str(temporary)}


def check_controller(output: Path) -> None:
    """Start a real RPC worker and reject tempfile's system-directory fallback."""
    output = output.resolve()
    env = controller_environment(output, dict(os.environ))
    os.environ["TMPDIR"] = env["TMPDIR"]
    with BaseManager(ctx=multiprocessing.get_context("spawn")) as manager:
        address = manager.address
        if not isinstance(address, str):
            raise GateError("Controller did not allocate a Unix socket")
        path = Path(address).resolve()
        if not path.is_relative_to(Path(env["TMPDIR"])):
            raise GateError(f"Controller socket escaped its private directory: {path}")
        length = len(os.fsencode(path))
        if length >= SOCKET_LIMIT:
            raise GateError(f"Controller socket exceeds its path budget: {path}")
        write_json(
            output / f"controller-python{platform.python_version()}.json",
            {
                "python": sys.version,
                "socket": str(path),
                "bytes": length,
                "status": "passed",
            },
        )


def main() -> int:
    """Run once in each admitted Python before the expensive matrix."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        check_controller(args.output)
    except (GateError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
