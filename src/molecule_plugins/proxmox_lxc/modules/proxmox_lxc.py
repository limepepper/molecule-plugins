#!/usr/bin/python

# Copyright (c) 2022, Sine Nomine Associates
# BSD 2-Clause License

ANSIBLE_METADATA = {
    'metadata_version': '1.1.',
    'status': ['preview'],
    'supported_by': 'community',
}

from ansible.module_utils.basic import AnsibleModule

def main():
    module = AnsibleModule(
        argument_spec=dict(
        ),
        supports_check_mode=True
    )

    result = {}
    result = dict(
        warnings="this is a warning",
        changed=False,
        api={"erigjroijg":"ijreoijrgo"},
        msg="successully cached credentials"
    )

    module.exit_json(**result)


if __name__ == '__main__':
    main()
