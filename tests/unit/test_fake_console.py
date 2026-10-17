# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The appliance fake product's console keeps the lab contract's limits."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.common import ROOT

FAKE = (
    ROOT
    / "collection_modules_appliance/tests/integration/targets/setup_fixture_fake"
    / "files/fake_product.py.j2"
)


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    script = tmp_path / "fake_product.py"
    script.write_text(
        "".join(
            line
            for line in FAKE.read_text().splitlines(keepends=True)
            if not line.startswith("# SPDX")
        ),
        encoding="utf-8",
    )
    (tmp_path / "state").mkdir()
    return script


def console(fake: Path, request: Any) -> tuple[int, dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, str(fake), "console", str(fake.parent / "state")],
        input=request if isinstance(request, str) else json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        env={"PATH": os.environ["PATH"]},
    )
    answer: dict[str, Any] = json.loads(result.stdout)
    return result.returncode, answer


@pytest.mark.parametrize(
    "request_",
    [
        "not json",
        {"label": "Upper", "commands": ["true"]},
        {"label": "x" * 49, "commands": ["true"]},
        {"label": "drill", "commands": []},
        {"label": "drill", "commands": ["true"] * 33},
        {"label": "drill", "commands": ["x" * 201]},
        {"label": "drill", "commands": ["printf 'a\\tb'\t"]},
    ],
)
def test_a_malformed_request_is_refused(fake: Path, request_: Any) -> None:
    assert console(fake, request_) == (2, {"status": "invalid"})


def test_commands_run_in_order_in_the_guest_until_one_fails(fake: Path) -> None:
    guest = fake.parent / "state/guest"
    code, answer = console(
        fake,
        {"label": "drill-backup", "commands": ["cp fixture-config.json good"] * 32},
    )
    assert code == 0 and answer["status"] == "ok" and answer["commands"] == 32
    assert json.loads((guest / "good").read_text())["antilockout"] is True
    code, answer = console(
        fake, {"label": "drill-restore", "commands": ["false", "touch reached"]}
    )
    assert code == 1 and answer["status"] == "failed"
    assert not (guest / "reached").exists()
