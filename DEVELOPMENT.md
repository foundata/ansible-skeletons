# Development

<!--
SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
SPDX-License-Identifier: GPL-3.0-or-later
-->

This repository ships skeletons for `ansible-galaxy`. Before a release, the
checks generate projects from all five skeletons, run their roles, and build
and install the generated collections.

All three module skeletons also render two identities and exercise installed
fixture modules against the `foundata.utils` release pinned in
`tests/requirements.yml`.


## Table of contents<a id="toc"></a>

- [Prerequisites](#prerequisites)
- [Getting started](#getting-started)
- [Project structure](#project-structure)
- [Development standards](#development-standards)
- [Testing](#testing)
  - [Quick checks](#quick-checks)
  - [Compatibility matrix](#compatibility-matrix)
  - [Infrastructure tests](#infrastructure-tests)
- [Updating dependencies](#updating-dependencies)
- [Releases](#releases)
  - [Release checklist](#release-checklist)
  - [Recovery](#recovery)
- [Troubleshooting](#troubleshooting)


## Prerequisites<a id="prerequisites"></a>

Install Git, [uv](https://docs.astral.sh/uv/), `shfmt`, `shellcheck`, and
`checkbashisms`. Python tools are installed from the committed `uv.lock`.
Python 3.12, 3.13 and 3.14 are needed for the full matrix; uv can supply them:

```sh
uv python install 3.12 3.13 3.14
uv sync --locked
```

uv installs the Python tools in isolated environments, foundata's
[`releasing`](https://foundata.com/en/projects/releasing/) among them.
`pyproject.toml` contains the development dependencies and configuration.


## Getting started<a id="getting-started"></a>

While editing templates or tooling, check what your changes affect:

```sh
./scripts/check.sh --changed
```

This runs the static checks, the repository tests and only the lanes and
module skeletons that your uncommitted changes affect. A change outside the
skeleton directories, such as in `scripts/` or `uv.lock`, selects everything.
The run prints the checks it skipped and never writes a passing
`checks.json`. It cannot be combined with `--resume` or `--integration`.
Before each commit, run the full quick check:

```sh
./scripts/check.sh
```

To retain generated projects for inspection, choose a directory that does not
already exist:

```sh
uv run --locked --group core221 python -m scripts.render \
  --output "${XDG_STATE_HOME:-${HOME}/.local/state}/ansible-skeletons/render-1"
```

The output contains a standalone role, a collection, and an isolated Ansible
configuration. Add `--defaults` to exercise initialization without extra
variables. The helper refuses to overwrite an existing directory.


## Project structure<a id="project-structure"></a>

|              Path               | Purpose |
| ------------------------------- | ------- |
| `role_default/`                 | Standalone role skeleton |
| `collection_default/`           | Collection skeleton, including its `run` role |
| `collection_modules_appliance/` | Management-path appliance module skeleton |
| `collection_modules_rest/`      | Module skeleton with an immediate-write REST fixture |
| `collection_modules_cli/`       | Command-line module skeleton with an existing-only executable fixture |
| `scripts/render.py`             | Render a role and the selected collection skeleton with Galaxy |
| `scripts/smoke.py`              | Validate rendering, artifacts and role behavior |
| `scripts/check.sh`              | Local quality-check entry point |
| `scripts/integration.py`        | Generated Molecule scenarios with active fixtures |
| `scripts/release.py`            | Validate a release commit, then hand the tag to `releasing` |
| `tests/unit/`                   | Release and helper regression tests |
| `tests/fixtures/`               | Disposable role execution fixtures |
| `tests/requirements.yml`        | Pinned Galaxy dependencies for the checks |
| `pyproject.toml`, `uv.lock`     | Tool configuration, matrix and locked Python tools |

Galaxy consumes one `.j2` suffix when creating a project. Files named
`*.j2.j2` intentionally become runtime `*.j2` templates. Likewise, `FIXME`
instructions and `.yml.example` files are intentional parts of the product.
Do not reject all remaining Jinja expressions or placeholders as render errors.

`FIXME.md` is a setup file: its last step removes it, and fleet backports never
carry it forward. A document that outlives setup keeps its own facts and marks
a setup step with `FIXME` text, which the checklist replaces. It never links
to `FIXME.md`.

Edit each skeleton directly in its own directory.
`scripts.render --profile collection_modules_rest` or
`--profile collection_modules_appliance` or `--profile collection_modules_cli`
renders a module skeleton. Their rendered `DEVELOPMENT.md` explains the
lifecycle base, the binding, the engine and each variant's fixtures. The CLI
variant has an existing-only hook resource and information and planning modules.
Only the appliance variant carries act-once activation, the management-path
guard scaffold and the recovery drill.

`tests/unit/test_checks.py` lists the files that skeletons share. Copies of a
shared file are identical except inside variant regions, which wrap the few
lines a skeleton needs differently:

```jinja
{# skeleton-variant: dependencies #}
dependencies:
  "foundata.utils": ">=1.0.0,<2.0.0"
{# end skeleton-variant #}
```

Every copy declares the same regions in the same order; a region may be empty.
Galaxy renders the marker lines to nothing. Change the shared text in every
copy at once.

A document that shares only part of its text with a group lists that section
or paragraph in `SHARED_PASSAGES`, with regular expressions that bound it. Each
bound must match once in every copy, and variant regions work as they do in a
shared file. A passage can span a wider group than the file's own: the
module-region test then keeps the regions a passage needs identical within the
module skeletons.


## Development standards<a id="development-standards"></a>

Follow the foundata
[Ansible playbooks](https://foundata.com/en/guidelines/ansible-playbooks/),
[Ansible roles and collections](https://foundata.com/en/guidelines/ansible-roles-collections/),
[shell](https://foundata.com/en/guidelines/shell-scripting/),
[Python](https://foundata.com/en/guidelines/python/),
and
[Markdown](https://foundata.com/en/guidelines/markdown/)
style guides. Use UTF-8 without a BOM, LF line endings, and a trailing newline.

Use Python for structured data and validation. Keep shell wrappers small. Add
regression tests for new behavior and bug fixes, and explain any lint
suppressions.

```sh
uv run --locked ruff format scripts tests
uv run --locked ruff check scripts tests
uv run --locked mypy scripts tests
```

Write template text for maximum-length names. The checks also render every
skeleton with a namespace, a collection name and a role name of 64 characters
each, and the generated project must pass its own linters there:

- **Markdown:** rumdl accepts a line of more than 80 characters only in a
  paragraph that is already wrapped as its formatter would wrap it. A paragraph
  in a `*.md.j2` that contains a Jinja name therefore needs rumdl's line breaks
  at the rendered length. To get them, copy the paragraph into a scratch file,
  replace each name with a 64-character word, run
  `uv run --locked rumdl fmt --config .rumdl.toml` on the copy and take over
  its line breaks. With short names the lines stay below the limit, and rumdl
  leaves such a paragraph alone.
- **YAML:** yamllint allows 160 characters, in the documentation of a module
  too. A line that holds `M({{ namespace }}.{{ collection_name }}.<module>)`
  beside other text can exceed that. Fold the item with `>-` and give the
  reference a line of its own.

Commit messages use one scoped subject, for example
`tests: verify generated collection installation`. Do not add attribution
trailers. Review the diff and stage only changes intended for the commit.


## Testing<a id="testing"></a>

Each check answers a different question. Use the cheapest one that answers
yours:

|                   Check                   |                                    Answers                                    |                    Run it                    | Time |
| ----------------------------------------- | ----------------------------------------------------------------------------- | -------------------------------------------- | ---- |
| `uv run --locked python -m pytest -q`     | Do this repository's scripts work?                                            | While changing `scripts/`                    | about 1 minute |
| `./scripts/check.sh --changed`            | Does the part I edited still work?                                            | While editing                                | depends on the change |
| `./scripts/check.sh`                      | Does every skeleton still generate projects that build and pass their checks? | Before each commit                           | about 6.5 minutes |
| `./scripts/check.sh --matrix`             | Does that hold on every supported ansible-core and Python?                    | After dependency changes                     | about 15 to 20 minutes |
| `./scripts/check.sh --integration <tier>` | Do generated roles work on real containers and VMs?                           | When a change reaches a scenario or platform | about 5 minutes per tier |
| [Release gate](#release-checklist)        | Does all of this pass for one clean commit?                                   | Once per release                             | about 20 minutes |

Times come from a 16-core workstation with the default eight jobs, November
2026.

### Quick checks<a id="quick-checks"></a>

Run `./scripts/check.sh` before each commit. A template can look fine and still
render something broken, so the check tests what the skeletons generate. It
reads your working tree, uncommitted changes included.

After the repository's own checks, it generates projects from every skeleton and
uses them as a consumer would. That catches mistakes such as:

- A long collection name that pushes a generated line past the YAML or Markdown
  limit.
- A repository-only file, such as `noxfile.py`, in the built collection archive.
- A generated project that fails its own checks, or that a run of its default
  sessions without `CI=true` changes. Its `ansible-lint` session only reports
  and must write nothing.
- An installed module that no longer behaves, a lost reply that does not settle,
  or a credential in verbose output.

The module skeletons render two identities, the second with maximum-length names
and a quoted product. Each runs its Molecule scenario against its own fake
product, and the appliance scenario also drills the recovery of two subsystems.

Independent steps run at the same time; `--jobs N` sets how many, by default
half the CPUs. Each step writes `logs/<step>.log` below the check's output, and
a failing step prints the end of its log. With more than one job, every step
whose prerequisites passed still runs, so a [resumed run](#check-results)
repeats only what failed. `--jobs 1` stops at the first failure.

To test a release candidate of foundata's ansible-lint rules, build its wheel
and pass it to override the released package:

```sh
./scripts/check.sh --foundata-wheel /absolute/path/to/ansible_lint_foundata-1.1.0rc1-py3-none-any.whl
```

To test against an unreleased `foundata.utils` commit, pass its source
directory with `--utils-source /path/to/utils` or set `SKELETONS_UTILS_SOURCE`.
Release checks always use the pinned release.

Use `python -m pytest` for the helper tests, so repository-local modules are
importable without an installation or test-specific `sys.path` changes. Warnings
are errors and pytest configuration is strict. Required suites must execute
without failures or skips.

#### Keep, resume and remove results<a id="check-results"></a>

Without `--output`, a check works in a temporary directory below `/var/tmp` and
deletes it at the end, logs included. To keep the results, name a new directory
that is not on a tmpfs such as `/tmp`, because a check writes several gigabytes
there, container storage included:

```sh
output="${XDG_STATE_HOME:-${HOME}/.local/state}/ansible-skeletons/check-1"
./scripts/check.sh --output "${output}"
```

A passing check keeps only its evidence: `run.json`, `records/`, `logs/`, the
JUnit results `unit-python*.xml`, and `checks.json` with the collection archives
it lists. A failed run keeps everything for diagnosis, and `--keep` does the
same for a passing one.

To continue a failed or stopped run, for example after a network timeout, pass
its output with the same options as before:

```sh
./scripts/check.sh --resume "${output}"
```

The resumed run repeats only the failed and missing steps and any step whose
options changed. It refuses when the sources changed: `records/` ties each
finished step to a digest of the tracked files and the untracked files Git does
not ignore, which are also all that renders read. A run also fails when the
sources change while it runs; `--changed` only warns. A passing run prunes its
output, which then cannot be resumed.

To delete an output you no longer need:

```sh
./scripts/check.sh --remove "${output}"
```

`--remove` takes one absolute path without symbolic links and refuses the
checkout, your home directory and their ancestors. The directory must hold the
`run.json` of a check, or a release gate's `gate.json` and `checks/run.json`.


### Compatibility matrix<a id="compatibility-matrix"></a>

The quick check tests one combination, the default ansible-core and Python. The
matrix repeats it on every supported pair, so a change that breaks only the
oldest or newest core cannot slip through. Run it after changing dependencies;
the release gate always includes it.

```sh
./scripts/check.sh --matrix
```

`[tool.skeletons]` in `pyproject.toml` defines the pairs, and each has its own
locked environment. Review them against the
[upstream maintenance policy](https://docs.ansible.com/ansible/latest/reference_appendices/release_and_maintenance.html#ansible-core-support-matrix)
when updating dependencies, and update the README compatibility statement with
them.

For the module skeletons, the matrix adds what takes too long for a quick check:

- `ansible-test` sanity, unit and integration sessions on every core, for the
  first identity.
- Sanity once on the newest released core for the second identity, because only
  sanity reads its maximum-length names.
- For the appliance skeleton, a drill of one subsystem alone through the
  standalone playbook, which must refuse an unknown subsystem and skip without a
  console contract.

Every drill in these checks runs against the fake product. A product's console
recovery is qualified only on the collection's authorized lab, before a
subsystem's first write that can affect the management path.

### Infrastructure tests<a id="infrastructure-tests"></a>

Overview:

|   Tier    |                               Coverage                               | Additional prerequisites |
| --------- | -------------------------------------------------------------------- | ------------------------ |
| `podman`  | Debian 13 and AlmaLinux 9, converge, idempotence, verify and destroy | Working rootless Podman  |
| `libvirt` | Debian 13 VM, converge, idempotence, verify and destroy              | Session libvirt, KVM access, QEMU, SSH and passt |
| `ee`      | Build an execution environment containing each generated artifact    | Rootless Podman; ansible-builder comes from uv |

```sh
./scripts/check.sh --integration podman
./scripts/check.sh --integration libvirt
./scripts/check.sh --integration ee
```

Tiers share Podman and libvirt state and run one at a time. They start once
the static checks, the repository tests and the default lane pass, while the
other quick steps continue.

The `ee` tier runs Molecule in the role's root, and the collection's scenario
through its nox session as a maintainer calls it,
`uv run noxfile.py -s molecule -- test --scenario-name ee`, which starts
Molecule inside `extensions/`. The session installs its tools, `ansible-builder`
among them, in the versions of `uv.lock`, and the tier keeps Python
environments, its own included, off the session's `PATH`. The role's scenario
calls the tier's `ansible-builder`.

For an infrastructure-only iteration:

```sh
uv run --locked --group core221 --group lint --group integration \
  python -m scripts.integration podman \
  --output "${XDG_STATE_HOME:-${HOME}/.local/state}/ansible-skeletons/podman-1"
```

Tests need network access for the pinned Galaxy collections, container images
and, for libvirt, the cloud image. A check downloads the Galaxy collections
once, into `galaxy/` below its output, and every later step, including the
module renders' Molecule scenarios, installs them from there.
`SKELETONS_VM_IMAGE` can name an absolute path to an existing
Debian-compatible cloud-init image. Each run sets a workspace of its own
(`MOLECULE_WORKSPACE`), which ends its VM and container names, and its VM
forwards SSH from a port the scenario picks. Podman tests use a separate
disposable storage configuration, including the execution-environment build and
its image cleanup.


## Updating dependencies<a id="updating-dependencies"></a>

Change Python dependency groups and regenerate `uv.lock` deliberately:

```sh
uv lock --upgrade
./scripts/check.sh --matrix --integration podman --integration libvirt --integration ee
```

Review lock changes before committing. Galaxy pins live in
`tests/requirements.yml`; update them together with infrastructure validation.
The module renders' Molecule scenarios install only from these pins, so the
file must list every collection those scenarios require.

The templates carry tool minimums of their own: `antsibull_docs_package` in
`antsibull-nox.toml.j2`, and the packages that the sessions in `noxfile.py.j2`
install, such as `ansible-lint-foundata>=1.2.0`. The checks run a generated
project's default sessions with their packages constrained to the versions
installed from `uv.lock`, so a minimum above the locked version cannot be
resolved. Raise a minimum in the templates and in the matching dependency group
of `pyproject.toml`, regenerate the lock, and commit them together.


## Releases<a id="releases"></a>

A release is a tag on a validated commit.

The changelog section, the tag, the push, the GitHub release entry and the
publication checks run through
[`releasing`](https://foundata.com/en/projects/releasing/), whose declaration is
[`.releasing.toml`](./.releasing.toml). What stays in this repository is the
skeleton-specific gate (`scripts/release.py check`).


### Release checklist<a id="release-checklist"></a>

1. Run `./scripts/check.sh --changed` while editing and `./scripts/check.sh`
   before each commit, and resolve failures.
2. Choose the next Semantic Versioning `X.Y.Z` version and move the Unreleased
   entries into a dated section. The command writes the section, its release
   link and the Unreleased comparison link, and leaves the placeholder entry
   under the Unreleased heading. Do not change the development project's
   `0.0.0` version in `pyproject.toml`.

   ```sh
   version='X.Y.Z'
   uv run --locked release changelog release "${version}"
   ```

3. Review and commit the release preparation:

   ```sh
   git diff --check
   git diff
   git add CHANGELOG.md
   git commit -m "release: prepare ${version}"
   git status --short
   ```

   Implementation changes must already be committed. The final status must be
   empty, including untracked files. Do not amend this commit after validation.
4. Run the release gate into a new output directory outside the checkout:

   ```sh
   state="${XDG_STATE_HOME:-${HOME}/.local/state}"
   evidence="${state}/ansible-skeletons/release-${version}"
   uv run --locked python -m scripts.release check "${version}" \
     --output "${evidence}"
   ```

   Do not use a tmpfs such as `/tmp`: the integration tests keep their
   container storage below the output. On a 16-core workstation, the gate's
   checks with all three tiers took about 20 minutes in November 2026.

   `check.log` shows one line per step as it starts and ends; each step's
   output is in `checks/logs/<step>.log`. A successful run creates
   `report.json`; a failure keeps its diagnostics but creates no passing
   report. Keep the whole evidence directory until step 9; the later steps
   read it.

   When a step fails for a reason outside the sources, such as a network
   timeout, or you stopped the gate, rerun the failed and missing steps into the
   same evidence:

   ```sh
   uv run --locked python -m scripts.release check "${version}" \
     --resume "${evidence}"
   ```

   The gate resumes only evidence of the same commit, base and backends. A
   resumed gate's report has the same form as a fresh one.
5. Create and inspect the local annotated tag:

   ```sh
   uv run --locked python -m scripts.release tag "${evidence}/report.json"
   git show "v${version}"
   ```

   The helper refuses unless the report belongs to `HEAD` with intact digests,
   the tree is clean, the remote answers, and no equal or newer release tag
   exists locally or on the remote. Nothing is pushed.
6. Push the branch, then the tag:

   ```sh
   uv run --locked release push "${version}"
   ```

   The branch goes first, so the tag never names a commit the remote has on no
   branch, and the command refuses a tag the branch does not contain.
7. Create the GitHub release from the changelog section. This needs an
   authenticated `gh`:

   ```sh
   uv run --locked release forge release-create "${version}"
   ```

8. Verify the publication:

   ```sh
   uv run --locked python -m scripts.release verify "${evidence}/report.json"
   ```

   The helper checks that the remote holds the tag and that the forge reports
   it as latest. It then clones the tag anonymously, checks that the clone's
   `HEAD` is the commit the gate tested, and renders, builds and installs each
   collection skeleton from the clone. The gate already tested that commit, so
   the helper repeats none of its tests: it starts no container or VM and needs
   no Galaxy.
9. Unless you archive the evidence, remove it:

   ```sh
   ./scripts/check.sh --remove "${evidence}"
   ```

Maintainers can bypass these local checks by invoking Git directly. Enforce
protected tags or equivalent forge rules separately if server-side enforcement
is needed. Any future CI should call the same commands.

### Recovery<a id="recovery"></a>

For a failed check, inspect its log, fix the cause, commit source changes, and
run the gate into a new evidence directory. `--resume` serves failures outside
the sources on the same commit only. Never edit a report or a step record to
turn a failure into success or carry it forward to another commit.

While no GitHub release exists, `uv run --locked release tag delete
"${version}"` removes a wrong tag locally and on the remote; it refuses once a
release exists. Do not move, replace or reuse a tag that consumers may have
checked out. Correct a defective release with a new patch version.

`uv run --locked release status "${version}"` reports where a release stands.
After an interrupted push or release creation, inspect the existing remote tag
and release entry and finish the missing publication step. Preserve the
validated commit and rerun publication verification when complete.


## Troubleshooting<a id="troubleshooting"></a>

- **The output path exists:** choose a new path; the helpers never merge a
  new result into an older run. To continue a failed run, use `--resume`.
- **The lock does not match:** review the dependency changes and regenerate
  the lock deliberately, as [Updating dependencies](#updating-dependencies)
  shows; release checks use `--locked`.
- **A socket path is too long:** the check binds its Unix sockets in a short
  private directory in `XDG_RUNTIME_DIR`, or `/tmp` without one, and refuses
  to start when a socket path there would exceed 103 bytes (Linux allows
  108). Shorten `XDG_RUNTIME_DIR`.
- **A tier's infrastructure is missing:** each tier's requirements are
  mandatory. Use quick checks on that machine, then run the release gate on a
  suitable host.
- **A run was stopped or killed:** continue it with `--resume` or give it up
  with `--remove`. Both first remove what it left running: the VMs it recorded
  and the containers in the Podman stores below its output, and no other VM or
  container of yours. A check killed outright also leaves its `sk-*` directory
  below `XDG_RUNTIME_DIR`, and a killed release gate its
  `ansible-skeletons-release-*` source below `TMPDIR`. Delete those yourself,
  with `podman unshare rm -rf`, because Podman's run state holds files of
  subordinate users. Ctrl-C and `kill` stop a check or release gate cleanly,
  and it ignores any further stop while it cleans up, for up to 15 minutes. A
  command past its timeout gets SIGTERM and, 15 seconds later, SIGKILL; an
  infrastructure tier gets those 15 minutes, because it destroys its VMs and
  containers before it exits.
- **A step failed:** its log is `logs/<step>.log` below the output, or
  `checks/logs/<step>.log` in release evidence. A failed run also keeps the
  generated YAML, the isolated `ansible.cfg`, collection archives and
  Molecule diagnostics; `--keep` keeps them after a passing run.
