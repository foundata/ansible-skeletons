# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The module skeletons' lost-reply harness, run against a stub collection.

The harness touches Ansible only through ``basic._ANSIBLE_ARGS`` and
``init_plugin_loader``, so small stubs stand in for ansible-core and
foundata.utils and the repository tests need neither.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.common import ROOT

HARNESS = "extensions/molecule/shared/files/loss.py.j2"

STUBS = {
    "ansible/__init__.py": "",
    "ansible/module_utils/__init__.py": "",
    "ansible/module_utils/basic.py": "_ANSIBLE_ARGS = None\n",
    "ansible/plugins/__init__.py": "",
    "ansible/plugins/loader.py": "def init_plugin_loader():\n    pass\n",
    "ansible_collections/foundata/utils/plugins/module_utils/errors.py": (
        "class TransportTimeout(Exception):\n"
        "    def __init__(self, code=None):\n"
        "        super().__init__(code)\n"
    ),
    "ansible_collections/acme/demo/plugins/module_utils/_client.py": (
        "STORE = {}\n"
        "\n"
        "class Client:\n"
        "    def write(self, path, value):\n"
        "        STORE[path] = value\n"
        "\n"
        "class Runner:\n"
        "    def send(self, command, value=None):\n"
        "        if value is not None:\n"
        "            STORE[command] = value\n"
    ),
    # Writes "value" in "stages" requests, one path each; "always" acts on
    # every run; "audit" sends an extra request outside the /items/ route.
    "ansible_collections/acme/demo/plugins/modules/thing.py": """
import json
import sys

from ansible.module_utils import basic
from ansible_collections.acme.demo.plugins.module_utils._client import (
    STORE,
    Client,
    Runner,
)
from ansible_collections.foundata.utils.plugins.module_utils.errors import (
    TransportTimeout,
)


def main():
    params = json.loads(basic._ANSIBLE_ARGS)["ANSIBLE_MODULE_ARGS"]
    name = params.get("name", "settings")
    runner = params.get("runner", False)
    changed = False
    for stage in range(params.get("stages", 1)):
        path = f"/items/{name}/{stage}"
        if STORE.get(path) == params["value"] and not params.get("always"):
            continue
        changed = True
        try:
            if runner:
                Runner().send(path, params["value"])
            else:
                Client().write(path, params["value"])
        except TransportTimeout:
            pass
        if STORE.get(path) != params["value"]:
            print(json.dumps({"failed": True, "changed": True}))
            sys.exit(1)
    if params.get("audit"):
        Client().write("/audit/log", name)
    print(json.dumps({"changed": changed, "verified": "observed"}))
    sys.exit(0)
""",
}


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    for relative, text in STUBS.items():
        path = tmp_path / "lib" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    source = (ROOT / "collection_modules_appliance" / HARNESS).read_text()
    script = tmp_path / "loss.py"
    script.write_text(
        "".join(
            line
            for line in source.splitlines(keepends=True)
            if not line.startswith("# SPDX")
        ),
        encoding="utf-8",
    )
    (tmp_path / "remote").mkdir()
    return tmp_path


def lose(harness: Path, **request: Any) -> subprocess.CompletedProcess[str]:
    request.setdefault("module", "acme.demo.thing")
    return subprocess.run(
        [sys.executable, str(harness / "loss.py")],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=harness,
        env={
            "PYTHONPATH": str(harness / "lib"),
            "ANSIBLE_REMOTE_TMP": str(harness / "remote"),
            "PATH": "/usr/bin:/bin",
        },
    )


def proof(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert result.returncode == 0, result.stderr
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def test_one_lost_write_settles_and_names_the_module_file(harness: Path) -> None:
    result = proof(
        lose(harness, identity={"name": "a"}, params={"name": "a", "value": "1"})
    )
    assert result["verified"] and result["writes"] == 1 and result["dropped"] == 1
    assert result["module_path"].endswith("acme/demo/plugins/modules/thing.py")
    assert result["identity"] == {"name": "a"} and result["rerun"] == "unchanged"


def test_a_module_without_identity_options_declares_an_empty_identity(
    harness: Path,
) -> None:
    assert proof(lose(harness, identity={}, params={"value": "1"}))["identity"] == {}
    missing = lose(harness, params={"value": "1"})
    assert missing.returncode != 0 and "Identity must be explicit" in missing.stderr
    differing = lose(harness, identity={"name": "a"}, params={"value": "1"})
    assert differing.returncode != 0 and "repeated" in differing.stderr


@pytest.mark.parametrize("drop,dropped", [("first", 1), ("all", 3)])
def test_staged_writes_are_counted_and_dropped(
    harness: Path, drop: str, dropped: int
) -> None:
    params = {"name": "s", "value": "1", "stages": 3}
    result = proof(
        lose(
            harness,
            identity={"name": "s"},
            params=params,
            expected_writes=3,
            drop=drop,
        )
    )
    assert result["writes"] == 3 and result["dropped"] == dropped


def test_the_expected_write_count_is_exact(harness: Path) -> None:
    result = lose(
        harness,
        identity={"name": "a"},
        params={"name": "a", "value": "1", "stages": 2},
    )
    assert result.returncode != 0 and "Expected 1 counted writes, saw 2" in (
        result.stderr
    )


def test_a_route_prefix_counts_only_matching_writes(harness: Path) -> None:
    params = {"name": "a", "value": "1", "audit": True}
    unrouted = lose(harness, identity={"name": "a"}, params=params)
    assert unrouted.returncode != 0
    routed = proof(
        lose(
            harness,
            identity={"name": "b"},
            params={**params, "name": "b"},
            route="/items/",
        )
    )
    assert routed["writes"] == 1


def test_an_operation_that_acts_on_every_run_skips_the_rerun(harness: Path) -> None:
    params = {"name": "a", "value": "1", "always": True}
    rerun = lose(harness, identity={"name": "a"}, params=params)
    assert rerun.returncode != 0
    result = proof(
        lose(
            harness,
            identity={"name": "b"},
            params={**params, "name": "b"},
            rerun="none",
        )
    )
    assert result["rerun"] == "none" and result["writes"] == 1


def test_another_transport_is_patched_by_its_target(harness: Path) -> None:
    params = {"name": "a", "value": "1", "runner": True}
    unpatched = lose(harness, identity={"name": "a"}, params=params)
    assert unpatched.returncode != 0
    result = proof(
        lose(
            harness,
            identity={"name": "b"},
            params={**params, "name": "b"},
            target="_client.Runner.send",
        )
    )
    assert result["writes"] == 1 and result["dropped"] == 1


@pytest.mark.parametrize(
    "option,value",
    [
        ("expected_writes", 0),
        ("expected_writes", True),
        ("drop", "some"),
        ("rerun", "twice"),
        ("route", 5),
        ("target", "os.system"),
        ("module", "not a module"),
    ],
)
def test_invalid_options_are_refused_before_any_write(
    harness: Path, option: str, value: Any
) -> None:
    result = lose(
        harness,
        identity={"name": "a"},
        params={"name": "a", "value": "1"},
        **{option: value},
    )
    assert result.returncode != 0 and "ValueError" in result.stderr
