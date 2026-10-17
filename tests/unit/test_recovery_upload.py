# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The appliance drill's upload plan, executed by a real shell."""

import importlib.util
import os
import re
import stat
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from scripts.common import ROOT

PLANNER = (
    ROOT
    / "collection_modules_appliance/extensions/molecule/shared/files"
    / "recovery_upload.py.j2"
)


@pytest.fixture
def upload(tmp_path: Path) -> ModuleType:
    source = "".join(
        line
        for line in PLANNER.read_text().splitlines(keepends=True)
        if not line.startswith("# SPDX")
    )
    path = tmp_path / "recovery_upload.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("recovery_upload", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def console(guest: Path, requests: list[dict[str, object]]) -> None:
    """Run each request the way the lab's console endpoint does."""
    for request in requests:
        label, commands = request["label"], request["commands"]
        assert isinstance(label, str) and isinstance(commands, list)
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", label)
        assert 0 < len(commands) <= 32
        for command in commands:
            assert len(command.encode()) <= 200 and command.isprintable()
            subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=guest,
                check=True,
                timeout=30,
                env={"PATH": os.environ["PATH"]},
            )


@pytest.mark.parametrize("size", [0, 1, 4096, 20000])
def test_a_file_arrives_whole_with_its_mode(
    upload: ModuleType, tmp_path: Path, size: int
) -> None:
    data = os.urandom(size)
    guest = tmp_path / "guest"
    (guest / "opt").mkdir(parents=True)
    plan = upload.plan(
        data, "opt/helper.bin", "nested", "base64 -d", "sha256sum", "0700"
    )
    assert plan["bytes"] == size and len(plan["requests"]) >= 1
    console(guest, plan["requests"])
    helper = guest / "opt/helper.bin"
    assert helper.read_bytes() == data
    assert stat.S_IMODE(helper.stat().st_mode) == 0o700
    assert sorted(path.name for path in (guest / "opt").iterdir()) == ["helper.bin"]


def test_a_wrong_digest_leaves_the_destination_alone(
    upload: ModuleType, tmp_path: Path
) -> None:
    guest = tmp_path / "guest"
    guest.mkdir()
    (guest / "helper").write_bytes(b"old")
    plan = upload.plan(b"new", "helper", "nested", "base64 -d", "cat", "0600")
    with pytest.raises(subprocess.CalledProcessError):
        console(guest, plan["requests"])
    assert (guest / "helper").read_bytes() == b"old"


@pytest.mark.parametrize(
    "destination,label,mode",
    [
        ("../escape", "nested", "0700"),
        ("with space", "nested", "0700"),
        ("quote'd", "nested", "0700"),
        ("x" * 121, "nested", "0700"),
        ("helper", "Nested", "0700"),
        ("helper", "nested", "u+x"),
    ],
)
def test_unsafe_plans_are_refused(
    upload: ModuleType, destination: str, label: str, mode: str
) -> None:
    with pytest.raises(ValueError, match=r"malformed|plain path"):
        upload.plan(b"data", destination, label, "base64 -d", "sha256sum", mode)


def test_a_destination_too_long_for_the_console_is_refused(
    upload: ModuleType,
) -> None:
    with pytest.raises(ValueError, match="limits"):
        upload.plan(
            b"data", "d/" * 59 + "x", "nested", "base64 -d", "sha256sum", "0700"
        )
