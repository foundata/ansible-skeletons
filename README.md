# Ansible (Galaxy) Skeletons

The repository provides various skeletons and blueprints to help with the creation of new [roles](https://docs.ansible.com/ansible/latest/user_guide/playbooks_reuse_roles.html) and [collections](https://docs.ansible.com/ansible/devel/dev_guide/developing_collections.html).

These skeletons follow several guidelines and best practices:

* [foundata: Ansible style guide](https://github.com/foundata/guidelines/blob/master/ansible-style-guide.md)
* [Red Hat's Coding Style Good Practices for Ansible](https://github.com/redhat-cop/automation-good-practices/blob/main/coding_style/README.adoc#ansible-guidelines)
* [Best Practices of the Ansible User guide](https://docs.ansible.com/ansible/latest/user_guide/playbooks_best_practices.html)



## Table of contents<a id="toc"></a>

- [How to use the Ansible skeletons](#usage)
- [Description of provided skeletons](#content)
  - [`collection_default`](#collection_default)
  - [`role_default`](#role_default)
- [Compatibility](#compatibility)
- [Contributing](#contributing)
- [Licensing, copyright](#licensing-copyright)
- [Author information](#author-information)



## How to use the Ansible skeletons<a id="usage"></a>

1. **Clone this repository and check out the latest release:**
   ```bash
   # Get the version number of the latest release
   version="$(curl -s -L https://api.github.com/repos/foundata/ansible-skeletons/releases/latest | jq -r '.tag_name' | sed -e 's/^v//g')"
   printf '%s\n' "${version}"

   # Clone and check out the latest release (you can switch versions anytime using "git checkout vX.Y.Z")
   git clone https://github.com/foundata/ansible-skeletons.git -b "v${version}"
   ```
   <br>
2. **Use `ansible-galaxy` to initialize a new collection or stand-alone role**. Provide the path to the desired skeleton, along with any necessary variable values (or let `ansible-galaxy` use default values), and specify a name for your new resource.<br>Examples:
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
      --extra-var "repository='https://FIXME.example.com/repo/'" \
      --extra-var "issues='https://FIXME.example.com/repo/issues/'" \
      --extra-var "homepage='https://FIXME.example.com'" \
      --extra-var "min_ansible_version='2.16.0'" \
      "namespace.new_collection"

   # Create a new stand-alone role called "new_role" based on the "role_default" skeleton
   # Syntax: ansible-galaxy role init --collection-skeleton <path> <extra variables> <name of the new role>
   ansible-galaxy role init \
      --role-skeleton "./role_default" \
      --extra-var "author='FIXME User <user@example.com>'" \
      --extra-var "company='FIXME your organization'" \
      --extra-var "repository_url='https://FIXME.example.com/repo/'" \
      --extra-var "issue_tracker_url='https://FIXME.example.com/repo/issues/'" \
      --extra-var "homepage_url='https://FIXME.example.com'" \
      --extra-var "min_ansible_version='2.16.0'" \
      "new_role"
   ```
   Additional Notes:
     - Names of namespaces, collections or roles must follow [some](https://docs.ansible.com/ansible/latest/dev_guide/developing_collections_structure.html#roles-directory) [rules](https://docs.ansible.com/ansible/latest/dev_guide/developing_collections_creating.html#naming-your-collection) and should consist of `a-z`, `0-9` and `_` only.
     - Adapt the directory name of the `--[collection|role]-skeleton` parameter value if you want to use another skeleton than `[collection|role_]default`. You can find a description of the available skeletons below.
3. Each created collection or stand-alone role includes a **`FIXME.md` file in its root directory** with further instructions about what to change to your needs.



## Description of provided skeletons<a id="content"></a>

The following list provides an overview of the available skeletons. You can also explore the subdirectories of this repository to examine their code. However, keep in mind that some parts may be difficult to read, as they contain [Jinja](https://palletsprojects.com/p/jinja/) code. This Jinja code is processed by `ansible-galaxy [collection|role]` with it's templating to generate the final files.


### `collection_default`<a id="collection_default"></a>

A general purpose skeleton to create new Ansible collection for [package and ship](https://redhat-cop.github.io/automation-good-practices/#_package_roles_in_an_ansible_collection_to_simplify_distribution_and_consumption) a `run`-role. Main features:

* Init tasks to check the environment and usage:
  * Role argument validation
  * Check for minimum Ansible version and supported operating systems / platform.
  * Automatic gathering of role-specific facts (useful with `gather_facts: false`)
  * Automatic search and include for [platform-specific variables](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_variables).
* Separation of logical task groups, automatic include for[ platform-specific tasks](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_tasks).
* Passes `ansible-lint --profile production --strict`.
* [`antsibull-changelog`](https://ansible.readthedocs.io/projects/antsibull-changelog/changelogs/) support.



### `role_default`<a id="role_default"></a>

A general purpose skeleton to create new Ansible stand-alone role. Main features:

* Init tasks to check the environment and usage:
  * Role argument validation
  * Check for minimum Ansible version and supported operating systems / platform.
  * Automatic gathering of role-specific facts (useful with `gather_facts: false`)
  * Automatic search and include for [platform-specific variables](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_variables).
* Separation of logical task groups, automatic include for [platform-specific tasks](https://redhat-cop.github.io/automation-good-practices/#_platform_specific_tasks).
* Passes `ansible-lint --profile production --strict`.



## Compatibility<a id="compatibility"></a>

The skeletons are designed to be compatible with all [supported](https://docs.ansible.com/ansible/latest/reference_appendices/release_and_maintenance.html#ansible-core-support-matrix) versions of `ansible-galaxy` and `ansible` that are not end-of-life and still receive patches. While older versions should also work as long as `ansible-core` is >= v2.16, we no might not explicitly test them.

The skeletons were explicitly tested with `ansible-galaxy` from the following `ansible` versions (descending order):

* `ansible-galaxy [core 2.18.2]`
* `ansible-galaxy [core 2.18.1]`

The following versions are known to be problematic:

* `ansible-galaxy [core 2.16.4]` ("ERROR! Invalid collection name" when passing `authors` as extra-var)



## Contributing<a id="contributing"></a>

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) if you want to get involved.

This project's functionality is mature, so there might be little activity on the repository in the future. Don't get fooled by this, the project is under active maintenance and used on daily basis by the maintainers.



## Licensing, copyright<a id="licensing-copyright"></a>

<!--REUSE-IgnoreStart-->
Copyright (c) 2020, 2023-2025 foundata GmbH (https://foundata.com)

This project is licensed under the GNU General Public License v3.0 or later (SPDX-License-Identifier: `GPL-3.0-or-later`), see [`LICENSES/GPL-3.0-or-later.txt`](LICENSES/GPL-3.0-or-later.txt) for the full text.

The [`REUSE.toml`](REUSE.toml) file provides detailed licensing and copyright information in a human- and machine-readable format. This includes parts that may be subject to different licensing or usage terms, such as third-party components. The repository conforms to the [REUSE specification](https://reuse.software/spec/). You can use [`reuse spdx`](https://reuse.readthedocs.io/en/latest/readme.html#cli) to create a [SPDX software bill of materials (SBOM)](https://en.wikipedia.org/wiki/Software_Package_Data_Exchange).
<!--REUSE-IgnoreEnd-->

[![REUSE status](https://api.reuse.software/badge/github.com/foundata/ansible-skeletons)](https://api.reuse.software/info/github.com/foundata/ansible-skeletons)



## Author information<a id="author-information"></a>

This project was created and is maintained by the following [foundata](https://foundata.com/) employees (alphabetical order):

* [Andreas Haerter](https://andreashaerter.com/) ([foundata](https://foundata.com/))
* [Frederik Meissner](https://meissner.im/) ([foundata](https://foundata.com/))

If you like it, you might [buy us a coffee](https://buy-me-a.coffee/ansible-skeletons/).
