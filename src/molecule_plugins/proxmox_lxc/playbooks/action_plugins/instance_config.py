#!/usr/bin/python
# ruff: noqa: UP008

import os
import syslog

from ansible.errors import (
    AnsibleActionFail,
)
from ansible.module_utils.six import string_types
from ansible.plugins.action import ActionBase
from ansible.utils.display import Display

display = Display()


class ActionModule(ActionBase):

    _supports_check_mode = True
    _supports_async = True

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = {}

        validation_result, new_module_args = self.validate_argument_spec(
            argument_spec={
                "proxmox_conf": {"type": "dict", "default": {}},
                "driver_defaults": {"type": "dict", "default": {}},
                "env_prefix": {"type": "str", "default": "PROXMOX_"},
                "scenario_name": {"type": "str", "default": None},
                "platforms": {"type": "list", "required": True},
                "instance_config": {"type": "list", "default": []},
                "verbosity": {"type": "int", "default": 0},
            },
        )

        syslog.syslog(f"validation_result {validation_result}")

        result = super(ActionModule, self).run(tmp, task_vars)
        del tmp  # tmp no longer has any effect
        result["instances"] = []

        env_prefix = self._task.args.get("env_prefix", None)
        driver_defaults = self._task.args.get("driver_defaults", None)
        platforms = self._task.args.get("platforms", None)
        scenario_name = self._task.args.get("scenario_name", None)
        proxmox_conf = self._task.args.get("proxmox_conf", None)
        instance_config = self._task.args.get("instance_config", None)

        if not isinstance(new_module_args["env_prefix"], string_types):
            msg = "Invalid type supplied for env_prefix, it must be a string"
            raise AnsibleActionFail(msg)

        if not isinstance(driver_defaults, dict):
            msg = "Invalid type supplied for driver_defaults, it must be a dict"
            raise AnsibleActionFail(msg)

        if not isinstance(platforms, list):
            msg = "Invalid type supplied for platforms, it must be a list"
            raise AnsibleActionFail(msg)

        if not (env_prefix and platforms):
            msg = "Invalid arguments supplied"
            raise AnsibleActionFail(msg)

        proxmox_vars = {k: v for k, v in os.environ.items() if k.startswith("PROXMOX_")}
        self._display.debug(
            f"Proxmox environment variables: {proxmox_vars}",
        )

        proxmox_conf_env = {
            "api_host": os.environ.get("PROXMOX_API_HOST"),
            "api_port": os.environ.get("PROXMOX_API_PORT"),
            "api_user": os.environ.get("PROXMOX_API_USER"),
            "api_password": os.environ.get("PROXMOX_API_PASSWORD"),
            "api_token_id": os.environ.get("PROXMOX_API_TOKEN_ID"),
            "api_token_secret": os.environ.get("PROXMOX_API_TOKEN_SECRET"),
            "node": os.environ.get("PROXMOX_API_NODE"),
        }

        proxmox_conf_env.update(proxmox_conf)
        proxmox_conf = proxmox_conf_env

        key_defaults = ["~/.ssh/id_ed25519", "~/.ssh/id_ecdsa", "~/.ssh/id_rsa"]

        # Get env var or None if not set
        identity_file = os.environ.get("PROXMOX_CT_IDENTITY_FILE")

        # If env var not set, look for first existing key file
        if not identity_file:
            for key_path in key_defaults:
                expanded_path = os.path.expanduser(key_path)
                if os.path.exists(expanded_path):
                    identity_file = expanded_path
                    break

        instance_defaults = {
            "identity_file": identity_file,
            "host_password": os.environ.get("PROXMOX_CT_PASSWORD"),
            "type": os.environ.get("PROXMOX_CT_TYPE", "instance"),
            "host_user": os.environ.get("PROXMOX_CT_USER", "root"),
            "host_port": os.environ.get("PROXMOX_CT_PORT", 22),
            "netifname": os.environ.get("PROXMOX_CT_NETIFNAME", "eth0"),
            "clone_type": os.environ.get("PROXMOX_CT_CLONE_TYPE", "opportunistic"),
            "netif": {
                "net0": "name="
                + os.environ.get("PROXMOX_CT_NETIFNAME", "eth0")
                + ",ip=dhcp,bridge=vmbr0,firewall=1",
            },
            "features": ["nesting=1"],
        }

        result["proxmox_conf"] = proxmox_conf
        result["instance_defaults"] = instance_defaults
        result["driver_defaults"] = driver_defaults

        _instanct_dict = {}
        if instance_config:
            for _instance in instance_config:
                _instanct_dict[_instance["instance"]] = _instance

        for platform in platforms:
            instance = instance_defaults.copy()
            instance.update(driver_defaults)
            instance.update(
                {
                    "proxmox_hostname": f"{scenario_name}-{platform['name']}".replace(
                        "_",
                        "-",
                    ),
                },
            )
            instance.update(platform)
            result["instances"].append(instance)

            if _instanct_dict.get(instance["name"]):
                _inst = _instanct_dict.get(instance["name"])
                if _inst["proxmox_hostname"] == instance["proxmox_hostname"]:
                    instance["vmid"] = int(_inst["vmid"])

        return result
