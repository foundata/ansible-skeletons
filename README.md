# Ansible (Galaxy) Skeletons

This repository contains different skeletons / blueprints to kickstart the creation of new [roles](https://docs.ansible.com/ansible/latest/user_guide/playbooks_reuse_roles.html) and [collections](https://docs.ansible.com/ansible/devel/dev_guide/developing_collections.html).

They follow several guidelines and best practices:

* [foundata: Ansible style guide](https://github.com/foundata/guidelines/blob/master/ansible-style-guide.md)
* [Red Hat's Coding Style Good Practices for Ansible](https://github.com/redhat-cop/automation-good-practices/blob/main/coding_style/README.adoc#ansible-guidelines) and
* [Best Practices of the Ansible User guide](https://docs.ansible.com/ansible/latest/user_guide/playbooks_best_practices.html)



## Table of contents

* [Usage](#usage)
* [Description of provided skeletons](#description-of-provided-skeletons)
  * [`role_default`](#role_default)
* [Author information](#author-information)
* [License, Copyright](#license-copyright)



## Usage

1. Clone this repository:
   ```sh
   git clone https://github.com/foundata/ansible-skeletons.git
   ```
2. Call `ansible-galaxy` and provide the path to the skeleton to use as well as a role name:
   ```sh
   ansible-galaxy role init --role-skeleton "./ansible-skeletons/role_default" "name_of_your_role"

   ansible-galaxy role init \
      --role-skeleton "./ansible-skeletons/role_default" \
      --extra-var "author='FIXME ${USER}'" \
      --extra-var "company='FIXME your org'" \
      --extra-var "repository_url='https://FIXME.example.com/repo/'" \
      --extra-var "issue_tracker_url='https://FIXME.example.com/repo/issues/'" \
      --extra-var "homepage_url='https://FIXME.example.com'" \
      --extra-var "min_ansible_version='2.16'" \
      "name_of_your_role"

   ```
   * `name_of_your_role` has to follow [some rules](https://galaxy.ansible.com/docs/contributing/creating_role.html#role-names) and should consist of `a-z`, `0-9` and `_` only.
   * Adapt the `--role-skeleton` parameter value if you want to use another skeleton than `role_default`. You can find a description of the available ones below.
   * Adapt the `--extra-var` lines as needed.
3. Have a look at the created `./name_of_your_role/FIXME.md` to get further instructions.



## Description of provided skeletons

The following lists gives an overview over the available skeletons. You can also have a look into the sub-directories of this repository to look at their code (even though the source might be partially hard to read. There is [Jinja](https://palletsprojects.com/p/jinja/) code which will be processed by `ansible-galaxy role init` to get the final files).



### `role_default`

A general purpose skeleton to create new Ansible Galaxy roles. Main features:

* Init tasks to check the execution environment (e.g. minimum Ansible version and supported operating systems), based on the role's meta data.
* Separation of logical task groups.


## Compatibility

The skeletons should be compatible with `ansible-galaxy` from [`ansible-core` version](https://docs.ansible.com/ansible/latest/reference_appendices/release_and_maintenance.html#ansible-core-support-matrix) 2.11 and newer. It was tested with:

* `ansible-galaxy [core 2.18.1]`
* `ansible-galaxy [core 2.14.8]`



## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) if you want to get involved.

This project's functionality is mature, so there might be little activity on the repository in the future. Don't get fooled by this, the project is under active maintenance and used on daily basis by the maintainers.


## Licensing, copyright

<!--REUSE-IgnoreStart-->
Copyright (c) 2020, 2023-2025 foundata GmbH (https://foundata.com)

This project is licensed under the GNU General Public License v3.0 or later (SPDX-License-Identifier: `GPL-3.0-or-later`), see [`LICENSES/GPL-3.0-or-later.txt`](LICENSES/GPL-3.0-or-later.txt) for the full text.

The [`REUSE.toml`](REUSE.toml) file provides detailed licensing and copyright information in a human- and machine-readable format. This includes parts that may be subject to different licensing or usage terms, such as third-party components. The repository conforms to the [REUSE specification](https://reuse.software/spec/). You can use [`reuse spdx`](https://reuse.readthedocs.io/en/latest/readme.html#cli) to create a [SPDX software bill of materials (SBOM)](https://en.wikipedia.org/wiki/Software_Package_Data_Exchange).
<!--REUSE-IgnoreEnd-->

[![REUSE status](https://api.reuse.software/badge/github.com/foundata/ansible-skeletons)](https://api.reuse.software/info/github.com/foundata/ansible-skeletons)


## Author information

This project was created and is maintained by the following [foundata](https://foundata.com/) employees (alphabetical order):

* [Andreas Haerter](https://andreashaerter.com/) ([foundata](https://foundata.com/))
* [Frederik Meissner](https://meissner.im/) ([foundata](https://foundata.com/))

If you like it, you might [buy us a coffee](https://buy-me-a.coffee/ansible-skeletons/).
