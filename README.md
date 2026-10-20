# Ansible (Galaxy) Skeletons

<!--
SPDX-FileCopyrightText: foundata GmbH <https://foundata.com>
SPDX-License-Identifier: GPL-3.0-or-later
-->

Opinionated blueprints for `ansible-galaxy role|collection init`.

Ansible Skeletons help you start new
[roles](https://docs.ansible.com/ansible/latest/user_guide/playbooks_reuse_roles.html)
and
[collections](https://docs.ansible.com/ansible/devel/dev_guide/developing_collections.html)
with a shared structure for tasks, metadata and tests.

The skeletons follow these style guides:

- [foundata: Ansible playbooks](https://foundata.com/en/guidelines/ansible-playbooks/)
- [foundata: Ansible roles and collections](https://foundata.com/en/guidelines/ansible-roles-collections/)
- [Red Hat's Coding Style Good Practices for Ansible](https://github.com/redhat-cop/automation-good-practices/blob/main/coding_style/README.adoc#ansible-guidelines)
- [Best Practices of the Ansible User guide](https://docs.ansible.com/ansible/latest/user_guide/playbooks_best_practices.html)


<!-- rumdl-disable MD033 -->
<!-- The project header uses HTML to size and center its logo. -->
<div align="center" id="project-readme-header">
<br>
<br>

<img src="./assets/images/logos/ansible-skeletons.svg" alt="Logo: Ansible (Galaxy) Skeletons" height="128" />

<br>
<br>

**⭐ Found this useful? Support open-source and star this project:**

[![GitHub repository](https://img.shields.io/github/stars/foundata/ansible-skeletons.svg)](https://github.com/foundata/ansible-skeletons)

<br>
</div>
<!-- rumdl-enable MD033 -->


## Table of contents<a id="toc"></a>

- [Features](#features)
- [Examples](#examples)
- [Usage](#usage)
- [Provided skeletons](#content)
  - [`collection_default`](#collection_default)
  - [`collection_modules`](#collection_modules)
  - [`collection_modules_appliance`](#collection_modules_appliance)
  - [`role_default`](#role_default)
- [Compatibility](#compatibility)
- [Contributing](#contributing)
- [Licensing, copyright](#licensing-copyright)
  - [Trademarks](#trademarks)
- [Author information](#author-information)


## Features<a id="features"></a>

- Configuration for
  [`ansible-lint`](https://docs.ansible.com/projects/lint/) and
  [Molecule](https://docs.ansible.com/projects/molecule/) (using Containers via
  Podman and/or VMs via libvirt).
- Separate files for OS-specific variables and tasks.
- Changelog conventions and release-note helpers.
- A common project structure and defaults for new roles and collections.


## Examples<a id="examples"></a>

Some collections built using these skeletons:

- `foundata.acmesh`:
  - GitHub: <https://github.com/foundata/ansible-collection-acmesh>
  - Galaxy: <https://galaxy.ansible.com/ui/repo/published/foundata/acmesh/>
- `foundata.sshd`:
  - GitHub: <https://github.com/foundata/ansible-collection-sshd>
  - Galaxy: <https://galaxy.ansible.com/ui/repo/published/foundata/sshd/>
- `foundata.postfix`:
  - GitHub: <https://github.com/foundata/ansible-collection-postfix>
  - Galaxy: <https://galaxy.ansible.com/ui/repo/published/foundata/postfix/>


## Usage<a id="usage"></a>

1. Clone this repository and check out the latest release:

   ```bash
   # Get the version number of the latest release
   version="$(curl -s -L https://api.github.com/repos/foundata/ansible-skeletons/releases/latest | jq -r '.tag_name' | sed -e 's/^v//g')"
   printf '%s\n' "${version}"

   # Clone and check out the latest release (you can switch versions anytime using "git checkout vX.Y.Z")
   git clone https://github.com/foundata/ansible-skeletons.git -b "v${version}"
   ```

   <br>
2. Use `ansible-galaxy` to initialize a new collection or standalone role.
   Provide the skeleton path and a name for the new resource. Pass any variable
   values you want to override, or use the defaults.<br>Examples:

   ```bash
   # Ensure ansible-galaxy is available and navigate to the cloned repository from step one
   ansible-galaxy --version
   cd ./path/to/ansible-skeletons

   # Create a new collection called "namespace.new_collection" based on the "collection_default" skeleton
   # Syntax: ansible-galaxy collection init --collection-skeleton <path> <extra variables> <name of the new collection including namespace>
   ansible-galaxy collection init \
      --collection-skeleton "./collection_default" \
      --extra-var '{"authors": ["FIXME User <user@example.com>"]}' \
      --extra-var "company='FIXME your organization'" \
      --extra-var "company_url='FIXME https://organization.example.com'" \
      --extra-var "description='Manage FIXME.'" \
      --extra-var "repository='https://FIXME.example.com/repo/'" \
      --extra-var "issues='https://FIXME.example.com/repo/issues/'" \
      --extra-var "homepage='https://FIXME.example.com'" \
      --extra-var "min_ansible_version='2.19.0'" \
      --extra-var "version='0.1.0'" \
      "namespace.new_collection"

   # Create a new stand-alone role called "new_role" based on the "role_default" skeleton
   # Syntax: ansible-galaxy role init --role-skeleton <path> <extra variables> <name of the new role>
   ansible-galaxy role init \
      --role-skeleton "./role_default" \
      --extra-var "author='FIXME User <user@example.com>'" \
      --extra-var "company='FIXME your organization'" \
      --extra-var "company_url='FIXME https://organization.example.com'" \
      --extra-var "description='Manage FIXME.'" \
      --extra-var "repository_url='https://FIXME.example.com/repo/'" \
      --extra-var "issue_tracker_url='https://FIXME.example.com/repo/issues/'" \
      --extra-var "homepage_url='https://FIXME.example.com'" \
      --extra-var "min_ansible_version='2.19.0'" \
      "new_role"
   ```

   Notes:
     - Names of namespaces, collections or roles must follow
       [some](https://docs.ansible.com/ansible/latest/dev_guide/developing_collections_structure.html#roles-directory)
       [rules](https://docs.ansible.com/ansible/latest/dev_guide/developing_collections_creating.html#naming-your-collection)
       and should consist of `a-z`, `0-9` and `_` only.
     - `company_url` is optional. When set, the copyright holder in license
       headers, `REUSE.toml` and the README notice becomes
       `<company> <<company_url>>`, for example
       `foundata GmbH <https://foundata.com>`. Galaxy's `company` field keeps
       the plain name.
     - Adapt the directory name of the `--[collection|role]-skeleton` parameter
       value to use a skeleton other than `[collection|role_]default`. The
       available skeletons are described below.
3. Follow the instructions in the generated `FIXME.md` file to adapt the
   collection or standalone role to your project.



## Provided skeletons<a id="content"></a>

Each skeleton has its own subdirectory. The source files contain
[Jinja](https://palletsprojects.com/p/jinja/) expressions that
`ansible-galaxy [collection|role]` renders when generating a project.
Edit each skeleton directly; none is generated from another.


### `collection_default`<a id="collection_default"></a>

A skeleton for
[packaging and shipping](https://redhat-cop.github.io/automation-good-practices/#_package_roles_in_an_ansible_collection_to_simplify_distribution_and_consumption)
a `run` role in an Ansible collection. It includes:

- Init tasks to check the environment and usage:
  - Role argument validation
  - Check for minimum Ansible version and supported operating systems /
    platform.
  - Automatic gathering of role-specific facts (useful with
    `gather_facts: false`)
  - Automatic search and include for
    [platform-specific variables](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_variables).
- Separation of logical task groups, automatic include for
  [platform-specific tasks](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_tasks).
- `.ansible-lint` and `.yamllint` configurations following the
  [foundata Ansible playbook guideline](https://foundata.com/en/guidelines/ansible-playbooks/#linting).
- [`antsibull-changelog`](https://docs.ansible.com/projects/antsibull-changelog/changelogs/)
  support.
- [`antsibull-nox`](https://docs.ansible.com/projects/antsibull-nox/) checks,
  including ansible-lint with foundata's rules, `ansible-test` sanity per
  `ansible-core` version and Molecule. The rendered `DEVELOPMENT.md` explains
  them.
- [Molecule](https://docs.ansible.com/projects/molecule/) support with a default
  scenario using [Podman](https://podman.io/docs/installation) and several
  [integration test targets](https://github.com/orgs/foundata/repositories?q=oci-*-itt).


### `collection_modules`<a id="collection_modules"></a>

A module-first skeleton for REST products whose writes take effect
immediately. For an appliance whose configuration carries its own management
path or whose activation is global, use
[`collection_modules_appliance`](#collection_modules_appliance) instead. It
includes:

- A shared lifecycle base that runs every resource module, and a definition
  engine for resources that a declaration can describe.
- A resource module and an info module against a fake REST product, with
  references, a write-only password and a deletion guard. The product's own
  resources replace them.
- A hook-based fixture with nested values and its info module, the starting
  point for resources that a definition cannot express.
- A Molecule scenario, and a harness that proves each module handles a lost
  reply to a create, update or delete.
- A verifying activation helper for products that activate changes in a
  separate step; the fixture does not use it.

Render it with a `metadata.json` and the product's display name:

```sh
cat > metadata.json <<'EOF'
{
  "authors": ["FIXME User <user@example.com>"],
  "company": "FIXME your organization",
  "company_url": "FIXME https://organization.example.com",
  "description": "Manage FIXME.",
  "repository": "https://FIXME.example.com/repo/",
  "issues": "https://FIXME.example.com/repo/issues/",
  "homepage": "https://FIXME.example.com"
}
EOF
ansible-galaxy collection init \
  --collection-skeleton ./collection_modules \
  --extra-vars @metadata.json \
  --extra-vars "skeleton_revision=$(git describe --tags --always)" \
  --extra-vars '{"product": "Example REST Product"}' namespace.product
```

`version` defaults to `1.0.0`; the core floor defaults to `2.19.0`. The
rendered collection requires `foundata.utils >=1.0.0`. Follow the rendered
`FIXME.md` to replace the fixtures with the product's resources.


### `collection_modules_appliance`<a id="collection_modules_appliance"></a>

A module-first skeleton for appliances with these traits:

- The managed configuration carries the management path, so a change can cut
  off the API the modules use.
- Activation is global: one apply step activates every pending change.
- Recovery through the console is rehearsed for each subsystem before the
  subsystem's first change that can affect the management path.

Choose it by those traits, not by the shape of the API. It requires a single
writer and a recovery path that does not depend on the API, such as a
console. On top of `collection_modules`, it adds:

- Act-once activation, for scopes where the product cannot confirm what it
  loaded. It reports `verified: unverifiable`, which does not prove that the
  requested state became active.
- A scaffold for a guard that keeps writes away from the management path.
- A recovery drill that backs up the configuration through the console, cuts
  off API access and restores the backup. Without console access, the drill
  skips with a reason.

Its fixtures run against a fake product with a grid-style API: UUID
identifiers, paginated searches, and add, set and delete actions. It contains
no deployment role. Render it with the same `metadata.json`:

```sh
ansible-galaxy collection init \
  --collection-skeleton ./collection_modules_appliance \
  --extra-vars @metadata.json \
  --extra-vars "skeleton_revision=$(git describe --tags --always)" \
  --extra-vars '{"product": "Example Grid"}' namespace.product
```

Follow the rendered `FIXME.md` to replace the fixtures with the product's
resources.


### `role_default`<a id="role_default"></a>

A skeleton for standalone Ansible roles. It includes:

- Init tasks to check the environment and usage:
  - Role argument validation
  - Check for minimum Ansible version and supported operating systems /
    platform.
  - Automatic gathering of role-specific facts (useful with
    `gather_facts: false`)
  - Automatic search and include for
    [platform-specific variables](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_variables).
- Separation of logical task groups, automatic include for
  [platform-specific tasks](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_tasks).
- `.ansible-lint` and `.yamllint` configurations following the
  [foundata Ansible playbook guideline](https://foundata.com/en/guidelines/ansible-playbooks/#linting).
- [Molecule](https://docs.ansible.com/projects/molecule/) support with a default
  scenario using [Podman](https://podman.io/docs/installation) and several
  [integration test targets](https://github.com/orgs/foundata/repositories?q=oci-*-itt).



## Compatibility<a id="compatibility"></a>

The release gate tests rendering and role execution with these controller
combinations, using the exact dependency versions recorded in `uv.lock`:

| Ansible core | Controller Python |
| ------------ | ----------------- |
| 2.19         | 3.12, 3.13        |
| 2.20         | 3.12, 3.13, 3.14  |
| 2.21         | 3.12, 3.13, 3.14  |

[Development: Testing](./DEVELOPMENT.md#testing) describes the checks and
their infrastructure coverage.

**ansible-core before 2.19 cannot run the rendered content:** the templates
write Jinja's own filters and tests as `ansible.builtin.<name>`, which
ansible-core resolves from 2.19. Projects rendered from the skeletons need only
Ansible and its supported Python runtime.



## Contributing<a id="contributing"></a>

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) if you want to get involved.

The maintainers use the skeletons daily and continue to maintain them, even
during periods with few repository updates.



## Licensing, copyright<a id="licensing-copyright"></a>

<!--REUSE-IgnoreStart-->
<!-- should match SPDX-PackageSupplier -->
Copyright (c) 2020, 2023-2026, foundata GmbH <https://foundata.com>

This project is licensed under the GNU General Public License v3.0 or later
(SPDX-License-Identifier: `GPL-3.0-or-later`), see
[`LICENSES/GPL-3.0-or-later.txt`](./LICENSES/GPL-3.0-or-later.txt) for the full
text.

The [`REUSE.toml`](./REUSE.toml) file provides detailed licensing and copyright
information in a human- and machine-readable format. This includes parts that
may be subject to different licensing or usage terms, such as third-party
components. The repository conforms to the
[REUSE specification](https://reuse.software/spec/). You can use
[`reuse spdx`](https://reuse.readthedocs.io/en/latest/readme.html#cli) to create
an
[SPDX software bill of materials (SBOM)](https://en.wikipedia.org/wiki/Software_Package_Data_Exchange).
<!--REUSE-IgnoreEnd-->

[![REUSE status](https://api.reuse.software/badge/github.com/foundata/ansible-skeletons)](https://api.reuse.software/info/github.com/foundata/ansible-skeletons)


### Trademarks<a id="trademarks"></a>

Third-party trademarks used in this repository:

- Ansible®, Fedora® and Red Hat® are trademarks of Red Hat, LLC, registered in
  the United States and other countries.
- Debian® is a trademark of Software in the Public Interest, Inc., registered in
  Germany and the United States.
- openSUSE® is a trademark of SUSE LLC or its affiliates, registered in the
  European Union and the United States.
- Proxmox® is a trademark of Proxmox Server Solutions GmbH, registered in
  Germany and the United States.
- Ubuntu® is a trademark of Canonical Ltd., registered in Germany, the European
  Union and the United States.
- Windows® is a trademark of Microsoft Corporation, registered in Germany, the
  European Union and the United States.

Their use here is purely descriptive and does not imply any affiliation with or
endorsement by the trademark holders.

Own and licensed trademarks used in this repository:

- foundata® is a trademark of [IPAM GmbH](https://ipam-services.com/),
  registered in Germany and the European Union, licensed to
  [foundata GmbH](https://foundata.com/).


## Author information<a id="author-information"></a>

This [project](https://foundata.com/en/projects/) was created and is maintained
by [foundata](https://foundata.com/). If you like it, you might
[buy us a coffee](https://buy-me-a.coffee/ansible-skeletons/).

The Ansible Skeletons project is *not* associated with
[Red Hat](https://www.redhat.com/) nor the
[Ansible project](https://ansible.com/).
