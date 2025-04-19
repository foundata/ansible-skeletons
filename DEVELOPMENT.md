# Development

This file provides additional information for maintainers and contributors.


## Testing

Nothing special or automated yet. Therefore just some hints for manual testing:

* Create dummy resources to see if everything renders and do linting. The helper script at the end of the file may help with that.
* Check if the outcome fits our [Ansible style guide](https://github.com/foundata/guidelines/blob/master/ansible-style-guide.md).
* Create resources with your skeleton for a simple program and test if everything works out conceptually as expected.


## Releases

1. Do proper [Testing](#testing). Continue only if everything is fine.
2. Determine the next version number. This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
3. Update the [`CHANGELOG.md`](./CHANGELOG.md). Insert a section for the new release. Do not forget the comparison link at the end of the file.
4. If everything is fine: commit the changes, tag the release and push:
   ```console
   version="<FIXME version>"
   git add "./CHANGELOG.md"
   git commit -m "Release preparations: v${version}"

   git tag "v${version}" "$(git rev-parse --verify HEAD)" -m "version ${version}"
   git show "v${version}"

   git push origin main --follow-tags
   ```
   If something minor went wrong (like missing `CHANGELOG.md` update), delete the tag and start over:
   ```console
   git tag -d "v${version}" # delete the old tag locally
   git push origin ":refs/tags/v${version}" # delete the old tag remotely
   ```
   This is *only* possible if there was no [GitHub release](https://github.com/foundata/ansible-skeletons/releases/). Use a new patch version number otherwise.
5. Use [GitHub's release feature](https://github.com/foundata/ansible-skeletons/releases/new), select the tag you pushed and create a new release:
   * Use `v<version>` as title
   * A description is optional. In doubt, use `See CHANGELOG.md for more information about this release.`
6. Check if the GitHub API delivers the correct version as `latest`:
   ```console
   curl -s -L https://api.github.com/repos/foundata/ansible-skeletons/releases/latest | jq -r '.tag_name' | sed -e 's/^v//g'
   ```


## Miscellaneous

### Encoding

* Use UTF-8 encoding with `LF` (Line Feed `\n`) line endings *without* [BOM](https://en.wikipedia.org/wiki/Byte_order_mark) for all files.

### Helper script

```bash
#!/usr/bin/env bash

################################################################################
# Helper for Ansible skeleton development (render, lint, and run the resources).
#
# This script performs the following actions:
#
# - Creates a temporary working directory including all necessary tools
#   (Python venv, Ansible, etc.)
# - Initializes an Ansible role using ansible-galaxy with the appropriate
#   skeleton
# - Initializes an Ansible collection using ansible-galaxy with the appropriate
#   skeleton
# - Lints the created resource
# - Runs a dummy playbook to instantiate the dummy resources
#
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: foundata GmbH (https://foundata.com)
################################################################################


################################################################################
# Miscellaneous constants and global values
################################################################################

# version of the script
readonly version="1.0.0"


################################################################################
# Environment
################################################################################
PATH='/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin'
LANG=en_US.UTF-8
LC_ALL="en_US.UTF-8"
set -u


################################################################################
# Command line arguments
################################################################################

# init
opt_source_dir_path="" # -s
opt_show_version="0" # -v

# parse options
opt=''
OPTIND='1'
while getopts ':hs:v' opt
do
    case "${opt}" in
        # path of the directory with the skeleton repository sources
        's')
            opt_source_dir_path="${OPTARG}"
            if ! [ -d "${opt_source_dir_path}" ]
            then
                opt_source_dir_path=''
                printf '%s: invalid value for "-%c", ignoring it.\n' "$(basename "${0}")" "${opt}" 1>&2
            fi
            ;;


        # show version flag
        'v')
            opt_show_version='1' # flag currently not being used, but still setting the value for code consistency
            printf '%s\n' "${version}"
            exit 0
            ;;

        # show help
        'h')
            filename="$(basename "${0}")"
            mantext="$(cat <<-DELIM
.TH ${filename} 1
.SH NAME
${filename} - Helper for Ansible skeleton development (render, lint, and
run the resources).

.SH SYNOPSIS
.B ${filename}
.PP
.BI "[-s " "/path/to/ansible-skeleton-sources" "]"

.SH DESCRIPTION
See https://github.com/foundata/ansible-skeletons/blob/main/DEVELOPMENT.md
for a detailed description.

.SH OPTIONS
.TP
.B -s
Path where the Ansible skeleton source repository can be found.
Defaults to "~/dev/ansible-skeletons"
.TP
.B -h
Print this help.
.TP
.B -v
Print the script's version number, then exit.

.SH EXIT STATUS
This program returns an exit status of zero if it succeeds. Non zero
is returned in case of failure. 2 will be returned for command line
syntax errors (e.g. usage of an unknown option).

.SH AUTHOR
Andreas Haerter <ah@foundata.com>
DELIM
)"
            if command -v 'mandoc' > /dev/null 2>&1
            then
                printf '%s' "${mantext}" | mandoc -Tascii -man | more
            elif command -v 'groff' > /dev/null 2>&1
            then
                printf '%s' "${mantext}" | groff -Tascii -man | more
            else
                printf '%s: Neither "mandoc" nor "groff" is available, cannot display help.\n' "$(basename "${0}")" 1>&2
                exit 1
            fi
            unset filename mantext
            exit 0
            ;;

        # unknown/not supported -> kill script and inform user
        *)
            printf '%s: unknown option "-%c" (or missing option value). Use "-h" to get usage instructions.\n' "$(basename "${0}")" "${OPTARG}" 1>&2
            exit 2
            ;;
    esac
done
unset opt OPTARG
shift $((${OPTIND} - 1)) && OPTIND='1' # delete processed options, reset index


################################################################################
# Config
################################################################################

if [ -z "${opt_source_dir_path}" ]
then
    readonly source_dir_path="${HOME}/dev/ansible-skeletons"
else
    readonly source_dir_path="${opt_source_dir_path}"
fi
readonly ansible_collection_namespace="foundata"
readonly ansible_collection_name="foobar"
readonly ansible_role_name="foobar"
readonly ansible_extravar_author_default="FIXME ${USER}"
readonly ansible_extravar_authors_default='["FIXME User <user@example.com>"]' # JSON list
readonly ansible_extravar_company_default="FIXME your org"
readonly ansible_extravar_desciption_collection_default="Ansible collection to manage foobar"
readonly ansible_extravar_desciption_role_default="Ansible role to manage foobar"
readonly ansible_extravar_repository_default="https://FIXME.example.com/repo/"
readonly ansible_extravar_issues_default="https://FIXME.example.com/repo/issues/"
readonly ansible_extravar_homepage_default="https://FIXME.example.com"
readonly ansible_extravar_min_ansible_version_default="2.16.0"


################################################################################
# Process
################################################################################


# check if needed commands and tools are available
for cmd in "declare" "mktemp" "python"
do
    if ! command -v "${cmd}" > /dev/null 2>&1
    then
        printf '%s:"%s" could not be found but is needed for execution.\n' "$(basename "${0}")" "${cmd}" 1>&2
        exit 1
    fi
done
unset cmd

# check the source dir
if ! [ -d "${source_dir_path}/.git" ]
   ! [ -d "${source_dir_path}/collection_default" ] ||
   ! [ -d "${source_dir_path}/role_default" ]
then
    printf '%s: "%s" is no valid Ansible skeleton source directory.\n' "$(basename "${0}")" "${source_dir_path}" 1>&2
    exit 1
else
    printf '%s: "%s" seems to be a valid Ansible skeleton source directory.\n' "$(basename "${0}")" "${source_dir_path}"
fi


# search if there is already a working directory
printf '%s: Searching for an already existing, temporary working directory...\n' "$(basename "${0}")"
: "${TMPDIR:=/tmp}" # if env var ${TMPDIR} is empty, set its value to /tmp
working_dir_found="false"
path_working_dir=""
for dir in "${TMPDIR}/ansible-skeletons-"*
do
    if [ -d "${dir}" ]
    then
        path_working_dir="${dir}"
        working_dir_found="true"
        printf '%s: Found an already existing working directory at "%s".\n' "$(basename "${0}")" "${path_working_dir}"
        break;
    fi
done

# create a new one if needed
if [ "${working_dir_found}" != "true" ]
then
    printf '%s: No working directory was found, going to to create one.\n' "$(basename "${0}")"
    # securely create a temporary dir
    mask_save="$(umask)"; umask 077 # temporarily change mask
    tempdir="$(mktemp -d "${TMPDIR}/ansible-skeletons-XXXXXXXXXXXXXX")" || tempdir='';
    umask "${mask_save}"; unset mask_save # restore mask
    if [ -z "${tempdir}" ] || ! [ -d "${tempdir}" ]
    then
        printf '%s: Creation of temporary directory failed:\n"%s"\n' "$(basename "${0}")" "${tempdir}" 1>&2
        exit 1
    fi
    path_working_dir="${tempdir}"
    printf '%s: Create a working directory at "%s".\n' "$(basename "${0}")" "${path_working_dir}"
fi
unset working_dir_found


# deactivate any virtual environment (there should be none in this script context but better safe than sorry)
if declare -F "deactivate" > /dev/null 2>&1
then
    deactivate
    printf '%s: Deactivated currently active Python virtual environment.\n"%s"\n' "$(basename "${0}")"
fi


# create python virtual environment if needed
printf '%s: Searching for an already existing Python virtual environment...\n' "$(basename "${0}")"
path_virtualenv="${path_working_dir}/.venv-ansible-skeletons"
path_pip="${path_virtualenv}/bin/pip"
if ! [ -d "${path_virtualenv}" ] ||
   ! [ -f "${path_pip}" ]
then
    if ! python -m "venv" "${path_virtualenv}"
    then
        printf '%s: Creation of Python virtual environment failed:\n"%s"\n' "$(basename "${0}")" "${path_virtualenv}" 1>&2
        exit 1
    fi
    printf '%s: Created a Python virtual environment at "%s".\n' "$(basename "${0}")" "${path_virtualenv}"
else
    printf '%s: Found an already existing Python virtual environment at "%s".\n' "$(basename "${0}")" "${path_virtualenv}"
fi


# activate new virtual environment
source "${path_virtualenv}/bin/activate"
pipcheck="$(which pip)"
if [ -z "${pipcheck}" ] ||
   [ "${pipcheck}" != "${path_pip}" ]
then
    printf '%s: Activation of virtual env at "%s" failed (pip not found under %s)\n' "$(basename "${0}")" "${path_virtualenv}" "${path_pip}" 1>&2
    exit 1
else
    printf '%s: Activated virtual env, printing pip and python path and versioning information:\n' "$(basename "${0}")"
    which pip
    which python
    pip --version
    python --version
fi


# create an Ansible config file
printf '%s: Creating "%s"\n' "$(basename "${0}")" "${path_working_dir}/ansible.cfg"
printf "%s\n" "$(cat <<-DELIM
## Ansible configuration for this env
# Debug hint: ansible-config dump --only-changed -t all

[defaults]
home = ${path_working_dir}/.ansible

## Dependencies
# - install collections into [current dir]/dependencies/ansible_collections/namespace/collection_name
# - install roles into [current dir]/dependencies/roles/role_name
collections_path = ${path_working_dir}/dependencies/collections
roles_path = ${path_working_dir}/dependencies/roles

display_args_to_stdout = true
error_on_undefined_vars = true
nocows = true
DELIM
)" > "${path_working_dir}/ansible.cfg"


# create a playbook
printf '%s: Creating "%s"\n' "$(basename "${0}")" "${path_working_dir}/playbook.yml"
printf "%s\n" "$(cat <<-DELIM
---

- hosts: localhost
  gather_facts: false
  tasks:

    - name: "Print a message"
      ansible.builtin.debug:
        msg: "This task runs before the example role (stand alone)"


    - name: "Include the example role (stand alone)"
      ansible.builtin.include_role:
        name: "${ansible_role_name}"
      vars:
        ${ansible_role_name}_service_state: "unmanaged"
      tags:
        - "always"
        - "${ansible_role_name}_always"
        - "${ansible_role_name}_setup"
        - "${ansible_role_name}_config"
        - "${ansible_role_name}_service"

    - name: "Print a message"
      ansible.builtin.debug:
        msg: "This task runs before the example role (from collection)"


    - name: "Include the example role (from collection)"
      ansible.builtin.include_role:
        name: "${ansible_collection_namespace}.${ansible_collection_name}.run"
      vars:
        run_${ansible_collection_name}_service_state: "unmanaged"
      tags:
        - "always"
        - "run_${ansible_collection_name}_always"
        - "run_${ansible_collection_name}_setup"
        - "run_${ansible_collection_name}_config"
        - "run_${ansible_collection_name}_service"
DELIM
)" > "${path_working_dir}/playbook.yml"


# install or upgrade needed tools
printf '%s: Installing or upgrading needed Ansible tools via pip.\n' "$(basename "${0}")"
pip install pip setuptools --upgrade
pip install ansible-core ansible-lint reuse antsibull-changelog --upgrade


# cleanup (best effort)
if command -v "gio" > /dev/null 2>&1
then
    # will likely fail as trashing on system internal mounts is not supported
    if gio trash "${path_working_dir}/dependencies" > /dev/null 2>&1
    then
        printf '%s: Moved "%s" into trash.\n' "$(basename "${0}")" "${path_working_dir}/dependencies"
    fi
fi
if [ -d "${path_working_dir}/dependencies/" ]
   [ -d "${path_working_dir}/dependencies/collections/" ] &&
   [ -d "${path_working_dir}/dependencies/roles/" ]
then
    if rm -rf "${path_working_dir}/dependencies/"
    then
        printf '%s: deleted "%s" (cleanup).\n' "$(basename "${0}")" "${path_working_dir}/dependencies/"
    fi
fi


# create needed directories
mkdir -p "${path_working_dir}/dependencies/collections/ansible_collections"
mkdir -p "${path_working_dir}/dependencies/roles"


# create the resources
printf '\n\n\n\n############################################################################\n'
printf '# %s: Creating a new collection based on\n' "$(basename "${0}")"
printf '# %s:   %s\n' "$(basename "${0}")" "${source_dir_path}/collection_default"
printf '# %s: in\n' "$(basename "${0}")"
printf '# %s:   %s\n' "$(basename "${0}")" "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}"
printf '############################################################################\n'
cd "${path_working_dir}/dependencies/collections/ansible_collections/"
set -x
ansible-galaxy collection init \
  --collection-skeleton "${source_dir_path}/collection_default" \
  --extra-var "{"authors": ${ansible_extravar_authors_default}}" \
  --extra-var "company='${ansible_extravar_company_default}'" \
  --extra-var "description='${ansible_extravar_desciption_collection_default}'" \
  --extra-var "repository='${ansible_extravar_repository_default}'" \
  --extra-var "issues='${ansible_extravar_issues_default}'" \
  --extra-var "homepage='${ansible_extravar_homepage_default}'" \
  --extra-var "min_ansible_version='${ansible_extravar_min_ansible_version_default}'" \
  --force \
  "${ansible_collection_namespace}.${ansible_collection_name}"
set +x

printf '\n\n\n\n############################################################################\n'
printf '# %s: Creating a new stand-alone role based on\n' "$(basename "${0}")"
printf '# %s:   %s\n' "$(basename "${0}")" "${source_dir_path}/role_default"
printf '# %s: in\n' "$(basename "${0}")"
printf '# %s:   %s\n' "$(basename "${0}")" "${path_working_dir}/dependencies/roles/${ansible_role_name}"
printf '############################################################################\n'
cd "${path_working_dir}/dependencies/roles"
set -x
ansible-galaxy role init \
  --role-skeleton "${source_dir_path}/role_default" \
  --extra-var "author='${ansible_extravar_author_default}'" \
  --extra-var "company='${ansible_extravar_company_default}'" \
  --extra-var "description='${ansible_extravar_desciption_role_default}'" \
  --extra-var "repository_url='${ansible_extravar_repository_default}'" \
  --extra-var "issue_tracker_url='${ansible_extravar_issues_default}'" \
  --extra-var "homepage_url='${ansible_extravar_homepage_default}'" \
  --extra-var "min_ansible_version='2.16.0'" \
  --force \
  "${ansible_role_name}"
set +x


# install dependencies needed for linting an executing the created resources
printf '%s: Installing or upgrading needed Ansible collections and roles via ansible-galaxy.\n' "$(basename "${0}")"
cd "${path_working_dir}"
for req_file in $(find "${path_working_dir}" -name "requirements.yml" -type f)
do
  printf '%s: Installing from %s.\n' "$(basename "${0}")" "${req_file}"
  set +x
  ansible-galaxy role install -r "${req_file}" # --requirements-file is not available for role install, using short option name
  ansible-galaxy collection install --requirements-file "${req_file}"
  set -x
done


# linting
printf '\n\n\n\n############################################################################\n'
printf '# %s: Linting the collection.\n' "$(basename "${0}")"
printf '############################################################################\n'
# Note: yaml[comments] is skipped as there are #comments without additional space,
#       which are related to FIXMEs and should be easy to remove without requiring
#       indentation adjustments afterwards.
set -x
ansible-lint --profile production --strict --skip-list yaml[comments] \
  "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}"
set +x

if [ -d "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/changelogs/fragments" ]
then
    set -x
    antsibull-changelog lint "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/changelogs/fragments"
    set +x
fi
if [ -f "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/changelogs/changelog.yaml" ]
then
    set -x
    antsibull-changelog lint-changelog-yaml "${path_working_dir}/dependencies/collections/ansible_collections/${ansible_collection_namespace}/${ansible_collection_name}/changelogs/changelog.yaml"
    set +x
fi

printf '\n\n\n\n############################################################################\n'
printf '# %s: Linting the stand-alone role.\n' "$(basename "${0}")"
printf '############################################################################\n'
# Note: yaml[comments] is skipped as there are #comments without additional space,
#       which are related to FIXMEs and should be easy to remove without requiring
#       indentation adjustments afterwards.
set -x
ansible-lint --profile production --strict --skip-list yaml[comments] \
  "${path_working_dir}/dependencies/roles/${ansible_role_name}"
set +x


# playbook run
printf '\n\n\n\n############################################################################\n'
printf '# %s: Starting a dummy playbook run.\n' "$(basename "${0}")"
printf '############################################################################\n'
cd "${path_working_dir}"
set -x
ansible-playbook "${path_working_dir}/playbook.yml"
set +x
cd "$(dirname "${0}")"


# deactivate any virtual environment (there should be none in this script context but better safe than sorry)
if declare -F "deactivate" > /dev/null 2>&1
then
    deactivate
    printf '%s: Deactivated currently active Python virtual environment.\n' "$(basename "${0}")"
fi


# open dir
if command -v "nautilus" > /dev/null 2>&1
then
    printf '%s: Opening working dir in GNOME Nautilus.\n' "$(basename "${0}")"
    nautilus "${path_working_dir}" > /dev/null 2>&1 &
fi


# misc cleanup tasks (best effort)
rm -rf "${path_working_dir}/dependencies/roles/.ansible"

exit 0
```