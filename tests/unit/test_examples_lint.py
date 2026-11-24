# SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""The examples lint must find every play and task list, and nothing else."""

import runpy
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.common import ROOT

HELPER = ROOT / "collection_default/tests/examples_lint.py.j2"


@pytest.fixture
def helper() -> dict[str, Any]:
    return runpy.run_path(str(HELPER))


README = """\
# Example

```yaml
- name: "Play"
  hosts: "all"
  tasks: []
```

~~~ YML title="tasks"
- name: "Task"
  ansible.builtin.debug:
    msg: "x"
~~~

```console
$ cat <<EOF
```yaml
not: "a block"
EOF
```

1. In a list:

   ````yaml
   - name: "Indented"
     ansible.builtin.debug:
       msg: "y"
   ```
   still inside
   ````

```yaml``` is inline code, not a fence.

```yaml
unclosed: true
"""


def test_markdown_blocks_follow_fences(helper: dict[str, Any]) -> None:
    blocks = helper["markdown_blocks"](README)
    assert blocks == [
        (3, ['- name: "Play"', '  hosts: "all"', "  tasks: []"]),
        (9, ['- name: "Task"', "  ansible.builtin.debug:", '    msg: "x"']),
        (
            24,
            [
                '- name: "Indented"',
                "  ansible.builtin.debug:",
                '    msg: "y"',
                "```",
                "still inside",
            ],
        ),
        (34, ["unclosed: true"]),
    ]
    lines = README.splitlines()
    assert lines[23].strip() == "````yaml" and lines[8].startswith("~~~")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('- hosts: "all"\n  tasks: []', "playbooks"),
        (
            '- name: "Play"\n  hosts: "all"\n- ansible.builtin.import_playbook: "x"',
            "playbooks",
        ),
        ('- import_playbook: "x.yml"', "playbooks"),
        ('- ansible.builtin.import_playbook: "x.yml"', "playbooks"),
        ('- name: "Task"\n  ansible.builtin.debug:\n    msg: "x"', "tasks"),
        ('- debug:\n    msg: "short names are linted, not skipped"', "tasks"),
        ('- name: "Loop"\n  ansible.builtin.debug: {}\n  with_items: [1]', "tasks"),
        ('- name: "Block"\n  block: []\n  rescue: []\n  when: "x"', "tasks"),
        ('- action: "ansible.builtin.ping"\n  register: "result"', "tasks"),
        ('- name: "Vault"\n  x.y.z:\n    secret: !vault |\n      $ANSIBLE', "tasks"),
        ('run_example_state: "absent"', None),
        ('all:\n  hosts:\n    one:\n      ansible_host: "192.0.2.1"', None),
        ('platforms:\n  - name: "molecule-debian13"\n    image: "debian:13"', None),
        ('- src: "https://example.com/role.git"\n  version: "main"', None),
        ('- "one"\n- "two"', None),
        ('- name: "<task name>"\n  <module>: {{ value }}', None),
        ("[]", None),
        ("", None),
        ('- name: "One"\n---\n- name: "Two"', None),
    ],
)
def test_only_plays_and_tasks_are_linted(
    helper: dict[str, Any], text: str, expected: str | None
) -> None:
    assert helper["kind"](text.splitlines()) == expected


MODULE = '''\
# Copyright line

DOCUMENTATION = r"""
module: example
"""

EXAMPLES = r"""
---
- name: "Example"
  ns.coll.example:
    name: "x"
"""
'''


def test_module_examples_are_read_without_running_the_module(
    helper: dict[str, Any],
) -> None:
    assert helper["module_examples"](MODULE + "raise SystemExit(1)\n") == [
        (7, ["", "---", '- name: "Example"', "  ns.coll.example:", '    name: "x"'])
    ]
    assert helper["module_examples"]('EXAMPLES = "a" + "b"\n') == []
    assert helper["module_examples"]("EXAMPLES = r'''\n{{ broken\n") == []
    assert helper["module_examples"]("DOCUMENTATION = ''\n") == []


@pytest.mark.parametrize(
    ("lines", "first", "expected"),
    [
        (["- name: x"], 4, ["#", "#", "---", "- name: x"]),
        (["---", "- name: x"], 4, ["#", "#", "#", "---", "- name: x"]),
        (["", "", "- name: x", "", ""], 2, ["#", "#", "---", "- name: x"]),
        (
            ["# a comment", "---", "- name: x"],
            2,
            ["#", "# a comment", "---", "- name: x"],
        ),
        (["- name: x"], 1, ["- name: x"]),
    ],
)
def test_written_examples_keep_the_source_line_numbers(
    helper: dict[str, Any], lines: list[str], first: int, expected: list[str]
) -> None:
    assert helper["numbered"](lines, first) == "\n".join(expected) + "\n"


def project(root: Path) -> Path:
    """A collection with examples in each place the lint reads."""
    (root / "tests").mkdir(parents=True)
    (root / "README.md").write_text(README, encoding="utf-8")
    role = root / "roles/run"
    role.mkdir(parents=True)
    (role / "README.md").write_text(
        "# Role\n\n```yaml\nrun_state: present\n```\n\n"
        '```yaml\n- name: "Use"\n  hosts: "all"\n  roles: ["ns.coll.run"]\n```\n',
        encoding="utf-8",
    )
    modules = root / "plugins/modules/nested"
    modules.mkdir(parents=True)
    (modules / "example.py").write_text(MODULE, encoding="utf-8")
    (root / "plugins/modules/__init__.py").write_text("", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs/guide.md").write_text(README, encoding="utf-8")
    (root / ".ansible-lint").write_text("---\nprofile: production\n")
    (root / ".yamllint").write_text("---\nextends: default\n")
    return root


def test_examples_are_extracted_with_the_lint_configuration(
    helper: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path / "collection")
    output = tmp_path / "examples"
    written = helper["extract"](root, output)
    assert written == [
        "playbooks/README.md-3.yml",
        "tasks/README.md-9.yml",
        "playbooks/roles-run-README.md-7.yml",
        "tasks/plugins-modules-nested-example.py-7.yml",
    ]
    assert sorted(path.name for path in output.iterdir()) == [
        ".ansible-lint",
        ".yamllint",
        "playbooks",
        "tasks",
    ]
    assert (output / ".ansible-lint").read_text() == "---\nprofile: production\n"
    printed = capsys.readouterr().out
    assert "README.md:24: skipped, not a list of plays or tasks" in printed
    assert "README.md:34: skipped" in printed
    assert "roles/run/README.md:3: skipped" in printed
    assert "docs/" not in printed
    readme = README.splitlines()
    play = (output / "playbooks/README.md-3.yml").read_text().splitlines()
    assert play[2:] == ["---", *readme[3:6]]
    task = (output / "tasks/README.md-9.yml").read_text().splitlines()
    assert task[9:] == readme[9:12]
    module = MODULE.splitlines()
    example = (output / written[-1]).read_text().splitlines()
    assert example[7:] == module[7:11] and example[:7] == ["#"] * 7
    with pytest.raises(FileExistsError):
        helper["extract"](root, output)


@pytest.mark.parametrize("status", [0, 2])
def test_ansible_lint_checks_the_examples_where_they_are(
    helper: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    root = project(tmp_path / "collection")
    output = tmp_path / "examples"
    calls: list[tuple[list[str], Path]] = []

    def run(command: list[str], *, cwd: Path, check: bool) -> Any:
        calls.append((command, cwd))
        assert not check and all((cwd / name).is_file() for name in command[5:])
        return subprocess.CompletedProcess(command, status)

    main = helper["main"]
    monkeypatch.setitem(main.__globals__, "__file__", str(root / "tests/x.py"))
    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr("sys.argv", ["examples_lint.py", str(output)])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == status
    [(command, cwd)] = calls
    assert command[1:5] == ["-m", "ansiblelint", "--offline", "--strict"]
    assert "--fix" not in command and len(command) == 9 and cwd == output


def test_a_project_without_examples_runs_no_lint(
    helper: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("# Empty\n\n```yaml\nkey: value\n```\n")
    main = helper["main"]
    monkeypatch.setitem(main.__globals__, "__file__", str(tmp_path / "tests/x.py"))
    monkeypatch.setattr("subprocess.run", None)
    monkeypatch.setattr("sys.argv", ["examples_lint.py", str(tmp_path / "out")])
    main()
    assert "No example is a list of plays or tasks" in capsys.readouterr().out
