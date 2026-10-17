# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Give a check's Unix sockets short private paths and exercise them."""

import argparse
import multiprocessing
import os
import platform
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing.managers import BaseManager
from pathlib import Path

from scripts.common import GateError, remove_tree, write_json

SOCKET_LIMIT = 104
# Python 3.12 uses listener-XXXXXXXX; 3.13 and 3.14 use sock-XXXXXXXXXXXX.
CONTROLLER_SUFFIX = "pymp-xxxxxxxx/listener-xxxxxxxx"
# Each step of a check run has its own runtime directory, <runtime>/<slot>, and
# its own TMPDIR, t/<slot> in the output; one character per slot.
STEP_SLOTS = "0123456789abcdefghijklmnopqrstuvwxyz"
# A check's sockets bind below a short directory of its own in the user's
# runtime directory; tempfile adds eight random characters to the prefix.
RUNTIME_PREFIX = "sk-"
RUNTIME_PLACEHOLDER = f"{RUNTIME_PREFIX}xxxxxxxx"
# Where a check tells each step, and the tools it runs, its runtime directory.
RUNTIME_VARIABLE = "SKELETONS_RUNTIME_DIR"
# TMPDIR is a link of this name in the step's runtime directory. Bulky
# temporary files stay on disk in the output; sockets get a short path.
TEMPORARY_LINK = "t"
# An infrastructure tier links a TMPDIR of its own below its output.
TIER_TEMPORARY_LINK = "i"
CONTROLLER_LAYOUT = f"{TEMPORARY_LINK}/{CONTROLLER_SUFFIX}"
# Python 3.14 binds the docs-check forkserver socket in the session's own tmp
# directory, inside its environment. The generated collection's nox
# environments stay in the render; nox reaches them through this link.
NOX_LINK = "n"
NOX_LAYOUT = f"{NOX_LINK}/docs-check-3-14/tmp/{CONTROLLER_SUFFIX}"
# An infrastructure tier's Podman keeps its XDG_RUNTIME_DIR, run root and
# temporary directory here, and would bind its API socket below it.
PODMAN_RUNTIME = "r"
PODMAN_LAYOUT = f"{PODMAN_RUNTIME}/podman/podman.sock"
# Molecule's SSH control sockets bind in this directory of an infrastructure
# tier's runtime directory. Molecule names them %h-%p-%r; OpenSSH binds the
# master socket under a 16-character temporary suffix first.
SSH_CONTROL_DIRECTORY = "s"
SSH_CONTROL_LAYOUT = f"{SSH_CONTROL_DIRECTORY}/127.0.0.1-65535-molecule.{'x' * 16}"


def check_runtime_layout(runtime: Path, *layouts: str) -> None:
    """Reject socket paths below a step's runtime directory that are too long."""
    for relative in (CONTROLLER_LAYOUT, *layouts):
        path = runtime / relative
        if len(os.fsencode(path)) >= SOCKET_LIMIT:
            raise GateError(
                f"The socket path {path} exceeds {SOCKET_LIMIT - 1} bytes; "
                "XDG_RUNTIME_DIR is too long"
            )


def runtime_base() -> Path:
    """Return the user's runtime directory, or /tmp where there is none."""
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    return Path(value) if value and Path(value).is_absolute() else Path("/tmp")


def private_directory(path: Path) -> Path:
    """Create a directory only its owner may use, or refuse an existing one."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    status = path.lstat()
    if path.is_symlink() or status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise GateError(f"{path} must be a directory private to its owner")
    return path


@contextmanager
def new_runtime_directory() -> Iterator[Path]:
    """Create a short private directory below XDG_RUNTIME_DIR; remove it after."""
    runtime = Path(tempfile.mkdtemp(prefix=RUNTIME_PREFIX, dir=runtime_base()))
    try:
        yield runtime
    finally:
        # Podman leaves files of subordinate users in its run state.
        remove_tree(runtime)


@contextmanager
def runtime_directory() -> Iterator[Path]:
    """Yield the short private directory for this process's Unix sockets.

    A check gives each step its own in SKELETONS_RUNTIME_DIR. A tool run on
    its own creates one and removes it at the end.
    """
    given = os.environ.get(RUNTIME_VARIABLE)
    if given:
        yield private_directory(Path(given))
        return
    with new_runtime_directory() as runtime:
        yield runtime


@contextmanager
def linked(link: Path, target: Path) -> Iterator[Path]:
    """Reach a deep directory through a short link while a tool runs there."""
    link.symlink_to(target, target_is_directory=True)
    try:
        yield link
    finally:
        link.unlink()


def temporary_environment(
    temporary: Path, link: Path, env: dict[str, str]
) -> dict[str, str]:
    """Point TMPDIR at a private directory in the output through a short link."""
    private_directory(temporary)
    link.symlink_to(temporary, target_is_directory=True)
    return {**env, "TMPDIR": str(link)}


def check_controller(output: Path) -> None:
    """Start a real RPC worker and reject tempfile's system-directory fallback.

    Python 3.13 and later bind below /tmp instead when TMPDIR is too long.
    """
    temporary = os.environ.get("TMPDIR", "")
    if not Path(temporary).is_absolute():
        raise GateError("The controller check needs an absolute TMPDIR")
    private_directory(Path(temporary).resolve())
    with BaseManager(ctx=multiprocessing.get_context("spawn")) as manager:
        address = manager.address
        if not isinstance(address, str):
            raise GateError("Controller did not allocate a Unix socket")
        # The socket's path as bound, through the link, is what the limit counts.
        path = Path(address)
        if not path.is_relative_to(temporary):
            raise GateError(f"Controller socket escaped its private directory: {path}")
        length = len(os.fsencode(path))
        if length >= SOCKET_LIMIT:
            raise GateError(f"Controller socket exceeds its path budget: {path}")
        output.mkdir(parents=True, exist_ok=True)
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
