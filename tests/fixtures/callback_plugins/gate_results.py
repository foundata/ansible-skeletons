"""Record observable role outcomes without parsing human-readable task output."""

import json
import os
from pathlib import Path
from typing import Protocol, override

from ansible.plugins.callback import CallbackBase


class _Task(Protocol):
    action: str

    def get_path(self) -> str: ...


class _Result(Protocol):
    _task: _Task
    _result: dict[str, object]


class CallbackModule(CallbackBase):  # type: ignore[misc] # Ansible's plugin base has no type stubs.
    """Record successful task outcomes for the smoke-test assertions."""

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "gate_results"
    CALLBACK_NEEDS_ENABLED = True

    @override
    def v2_runner_on_ok(self, result: _Result) -> None:
        """Append one data-only outcome supplied by Ansible's callback API."""
        task = result._task
        data = result._result
        record = {
            "task_path": task.get_path().rsplit(":", 1)[0],
            "action": task.action,
            "changed": data.get("changed", False),
            "package_state": data.get("gate_package_state"),
        }
        with Path(os.environ["SKELETONS_RESULTS"]).open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(record) + "\n")
