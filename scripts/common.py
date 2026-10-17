# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared process, configuration and filesystem operations for local gates."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[1]


class GateError(RuntimeError):
    """Report an unsuccessful validation with actionable context."""


@dataclass(frozen=True)
class Lane:
    """Identify one locked Ansible and controller Python combination."""

    python: str
    group: str

    @property
    def name(self) -> str:
        """Return a stable identifier for logs and release reports."""
        return f"{self.group}-python{self.python}"


def configuration(root: Path = ROOT) -> dict[str, object]:
    """Read the repository's development policy without resolving dependencies."""
    with (root / "pyproject.toml").open("rb") as stream:
        raw = tomllib.load(stream)["tool"]["skeletons"]
    return dict(raw)


def matrix(root: Path = ROOT) -> list[Lane]:
    """Read and validate the required compatibility matrix."""
    rows = configuration(root)["matrix"]
    if not isinstance(rows, list) or not rows:
        raise GateError("The compatibility matrix must not be empty")
    lanes = []
    for row in rows:
        if not isinstance(row, dict):
            raise GateError("Invalid compatibility matrix row")
        python, group = row.get("python"), row.get("group")
        if not isinstance(python, str) or not isinstance(group, str):
            raise GateError("Matrix rows need a Python version and dependency group")
        lanes.append(Lane(python, group))
    if len(set(lanes)) != len(lanes):
        raise GateError("Duplicate compatibility matrix row")
    return lanes


def run(
    command: Sequence[str],
    *,
    cwd: Path = ROOT,
    env: Mapping[str, str] | None = None,
    timeout: int = 600,
    capture: bool = False,
    allowed_exit_codes: tuple[int, ...] = (0,),
    log: TextIO | None = None,
) -> str:
    """Run a bounded command, raising on failure and preserving its diagnostics.

    With ``log``, the command line and its output go to that stream instead of
    the inherited standard output and error.
    """
    print(f"+ {' '.join(command)}", file=log, flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE if capture else log,
            stderr=subprocess.STDOUT if capture or log else None,
        )
        if result.returncode not in allowed_exit_codes:
            raise subprocess.CalledProcessError(
                result.returncode, command, output=result.stdout
            )
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, file=log, flush=True)
        raise GateError(
            f"{command[0]} failed with exit status {exc.returncode}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"{command[0]} exceeded its {timeout}s timeout") from exc
    return result.stdout or ""


def remove_in_user_namespace(paths: Sequence[Path]) -> None:
    """Remove paths as root of Podman's user namespace.

    Containers write files as subordinate users; only there are they ours.
    """
    run(["podman", "unshare", "rm", "-rf", "--", *map(str, paths)])


def remove_tree(
    path: Path,
    as_root: Callable[[Sequence[Path]], None] = remove_in_user_namespace,
) -> None:
    """Remove a directory tree, also where its owner withdrew write permission.

    What belongs to another user, such as a container's files, goes through
    ``as_root``.
    """

    def own(target: Path) -> None:
        if target.lstat().st_uid != os.getuid():
            raise PermissionError(f"{target} belongs to another user")
        target.chmod(0o700)

    def retry(_function: Callable[..., Any], name: str, error: BaseException) -> None:
        target = Path(name)
        if isinstance(error, FileNotFoundError):
            return
        if not isinstance(error, PermissionError):
            raise error
        if target != path:
            own(target.parent)
        if target.is_dir() and not target.is_symlink():
            own(target)
            shutil.rmtree(target, onexc=retry)
        else:
            target.unlink()

    try:
        shutil.rmtree(path, onexc=retry)
    except PermissionError:
        as_root([path])


def require_tools(*names: str) -> None:
    """Fail before running a gate when a required system tool is unavailable."""
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise GateError(f"Required tools are missing: {', '.join(missing)}")


def digest(path: Path) -> str:
    """Return a file's SHA-256 without loading the file into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_digest(root: Path = ROOT) -> str:
    """Digest the sources a check reads: each file's path, mode and content.

    A checkout contributes its tracked files and the untracked ones Git does
    not ignore, as they are in the working tree. An exported release has no
    Git metadata; the release helper supplies the digest of its clean checkout
    in SKELETONS_SOURCE_DIGEST.
    """
    if not (root / ".git").exists():
        supplied = os.environ.get("SKELETONS_SOURCE_DIGEST", "")
        if not re.fullmatch(r"[0-9a-f]{64}", supplied):
            raise GateError("Cannot identify the sources outside a Git checkout")
        return supplied
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    total = hashlib.sha256()
    for name in sorted(set(listing.split(b"\0")) - {b""}):
        path = root / os.fsdecode(name)
        if path.is_symlink():
            kind, content = b"link", os.fsencode(path.readlink())
        elif path.is_file():
            executable = path.stat().st_mode & 0o111
            kind, content = (b"exec" if executable else b"file"), path.read_bytes()
        else:
            # A tracked file deleted from the working tree.
            kind, content = b"gone", b""
        total.update(name + b"\0" + kind + b"\0")
        total.update(hashlib.sha256(content).digest())
    return total.hexdigest()


def write_json(path: Path, value: object) -> None:
    """Write UTF-8 structured data with stable formatting."""
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def isolated_environment(work: Path) -> dict[str, str]:
    """Give Ansible its own configuration, plugin paths, caches and dependencies."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ANSIBLE_", "MOLECULE_", "PYTHONPATH"))
    }
    home = work / "ansible-home"
    home.mkdir(parents=True, exist_ok=True)
    for directory in ("home", "cache"):
        (work / directory).mkdir(exist_ok=True)
    config = work / "ansible.cfg"
    config.write_text(
        "[defaults]\n"
        "retry_files_enabled = false\n"
        "host_key_checking = false\n"
        "nocows = true\n",
        encoding="utf-8",
    )
    env.update(
        {
            "ANSIBLE_CONFIG": str(config),
            "ANSIBLE_HOME": str(home),
            "ANSIBLE_COLLECTIONS_PATH": str(work / "collections"),
            "ANSIBLE_COLLECTIONS_SCAN_SYS_PATH": "false",
            "ANSIBLE_ROLES_PATH": str(work / "roles"),
            "ANSIBLE_LOCAL_TEMP": str(work / "local-tmp"),
            # A local connection's remote_tmp ignores HOME and would use the
            # user's own ~/.ansible/tmp.
            "ANSIBLE_REMOTE_TMP": str(work / "remote-tmp"),
            "ANSIBLE_NOCOLOR": "true",
            "PYTHONNOUSERSITE": "1",
            "UV_LINK_MODE": "copy",
            "HOME": str(work / "home"),
            "XDG_CACHE_HOME": str(work / "cache"),
        }
    )
    for kind in (
        "ACTION",
        "BECOME",
        "CACHE",
        "CALLBACK",
        "CONNECTION",
        "FILTER",
        "LOOKUP",
        "TEST",
        "VARS",
    ):
        env[f"ANSIBLE_{kind}_PLUGINS"] = str(home / "plugins" / kind.lower())
    env["ANSIBLE_LIBRARY"] = str(home / "plugins" / "modules")
    return env


def uv_command(
    lane: Lane,
    *arguments: str,
    groups: Sequence[str] = (),
    packages: Sequence[str] = (),
) -> list[str]:
    """Select a locked, throwaway tool environment for one matrix lane."""
    command = [
        "uv",
        "run",
        "--locked",
        "--isolated",
        "--python",
        lane.python,
        "--group",
        lane.group,
    ]
    for group in groups:
        command.extend(["--group", group])
    for package in packages:
        command.extend(["--with", package])
    return [*command, *arguments]
