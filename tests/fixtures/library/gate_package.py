"""Exercise package task arguments without modifying the controller host."""

from ansible.module_utils.basic import AnsibleModule


def main() -> None:
    """Accept only the deterministic package and states the smoke fixture uses."""
    module = AnsibleModule(
        argument_spec={
            "name": {"type": "list", "elements": "str", "required": True},
            "state": {"type": "str", "choices": ["present", "absent", "latest"]},
        },
        supports_check_mode=True,
    )
    if module.params["name"] != ["gate-example"]:
        module.fail_json(msg="Unexpected smoke-test package")
    module.exit_json(changed=False, gate_package_state=module.params["state"])


if __name__ == "__main__":
    main()
