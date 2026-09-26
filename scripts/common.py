"""Shared process, configuration and filesystem operations for local gates."""

import hashlib
import json
import os
import shutil
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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
) -> str:
    """Run a bounded command, raising on failure and preserving its diagnostics."""
    print(f"+ {' '.join(command)}", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, flush=True)
        raise GateError(
            f"{command[0]} failed with exit status {exc.returncode}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GateError(f"{command[0]} exceeded its {timeout}s timeout") from exc
    return result.stdout or ""


def require_tools(*names: str) -> None:
    """Fail before running a gate when a required system tool is unavailable."""
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise GateError(f"Required tools are missing: {', '.join(missing)}")


def digest(path: Path) -> str:
    """Return a file's SHA-256 without loading the file into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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
            "ANSIBLE_NOCOLOR": "true",
            "PYTHONNOUSERSITE": "1",
            "UV_LINK_MODE": "copy",
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


def uv_command(lane: Lane, *arguments: str, groups: Sequence[str] = ()) -> list[str]:
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
    return [*command, *arguments]
