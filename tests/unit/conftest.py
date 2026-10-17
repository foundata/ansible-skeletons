# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep every test out of the user's runtime directory and the running check's."""

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.controller import RUNTIME_VARIABLE


@pytest.fixture(autouse=True)
def runtime_base(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # A check runs these tests as one of its steps, with a runtime directory of
    # that step in the environment.
    monkeypatch.delenv(RUNTIME_VARIABLE, raising=False)
    with tempfile.TemporaryDirectory(prefix="run-") as directory:
        monkeypatch.setenv("XDG_RUNTIME_DIR", directory)
        yield Path(directory)
