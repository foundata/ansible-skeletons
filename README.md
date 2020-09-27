# Ansible (Galaxy) Skeletons

This repository contains different skeletons / blueprints to kickstart the creation of new [roles](https://docs.ansible.com/ansible/latest/user_guide/playbooks_reuse_roles.html) and [collections](https://docs.ansible.com/ansible/devel/dev_guide/developing_collections.html).



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
   ```
   * `name_of_your_role` has to follow [some rules](https://galaxy.ansible.com/docs/contributing/creating_role.html#role-names) and should consist of `a-z`, `0-9` and `_` only.
   * Adapt the `--role-skeleton` parameter value if you want to use another skeleton than `role_default`. You can find a description of the available ones below.
3. Have a look at the created `./name_of_your_role/FIXME.md` to get further instructions.



## Description of provided skeletons

The following lists gives an overview over the available skeletons. You can also have a look into the sub-directories of this repository to look at their code (even though the source might be partially hard to read. There is [Jinja](https://palletsprojects.com/p/jinja/) code which will be processed by `ansible-galaxy role init` to get the final files).



### `role_default`

A general purpose skeleton to create new Ansible Galaxy roles. Main features:

* Init tasks to check the execution environment (e.g. minimum Ansible version, OS plattform support), based on the role's meta data.
* Easy management of role parameters and belonging [`assert`](https://docs.ansible.com/ansible/latest/collections/ansible/builtin/assert_module.html) rules for validation (see `{{ role_name }}_required_vars` in [`role_default/vars/main.yml.j2`](./role_default/vars/main.yml.j2#L20) for more information).
* Separation of logical task groups.



## Author information

This project was created and is maintained by:

* [Andreas Haerter](https://andreashaerter.com/) ([Foundata](https://foundata.com/))
* [Frederik Meissner](https://meissner.im/) ([Foundata](https://foundata.com/))

If you like it, you might [buy us a coffee](https://buy-me-a.coffee/ansible-skeletons/).



## License, Copyright

[Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0) if not mentioned otherwise. See [`LICENSE`](./LICENSE) and [`NOTICE`](./NOTICE) for details.
