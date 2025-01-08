#!/usr/bin/python
# Make coding more python3-ish, this is required for contributions to Ansible
# ruff: noqa: UP032, SLF001, UP008
import json
import logging
import syslog
import typing as t
from io import StringIO

from ansible.errors import (
    AnsibleActionFail,
)
from ansible.module_utils.compat.datetime import utcnow
from ansible.plugins.action import ActionBase
from ansible.utils.display import Display
from ansible.utils.vars import merge_hash
from pygelf import GelfTcpHandler
from rich.console import Console

from molecule_plugins.proxmox_lxc.playbooks.module_utils.async_executor import (
    AnsibleAsyncExecutor,
)
from molecule_plugins.proxmox_lxc.playbooks.module_utils.molecule_proxmox_lxc import (
    LxcNode,
    LxcNodeAction,
    LxcNodeStatus,
    function_from_module_utils,
    get_cluster_info,
)

display = Display()

GRAYLOG_PORT = 12201
GRAYLOG_SERVER_IP = "docker.lan"

logger = logging.getLogger("proxmox_lxc")
logger.setLevel(logging.INFO)
logger.addHandler(
    GelfTcpHandler(
        host=GRAYLOG_SERVER_IP,
        port=GRAYLOG_PORT,
        include_extra_fields=True,
    ),
)


def rich_to_ansi(data: dict) -> str:
    # Create a StringIO buffer to capture Rich output
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True)

    # Render the dictionary to the console
    console.print(data)

    # Get the ANSI-encoded string
    return buffer.getvalue()


def check_unique_name(name, vms_by_name):
    if name in vms_by_name:
        display.warning(f"Found {name} in vms_by_name")
        vmids = vms_by_name[name]
        if len(vmids) > 1:
            raise AnsibleActionFail(f"Multiple VMs found with name {name}")
        return vmids[0]
    return None


class ActionModule(ActionBase):
    _supports_check_mode = True
    _supports_async = True

    def _combine_task_result(
        self,
        result: dict[str, t.Any],
        task_result: dict[str, t.Any],
    ) -> dict[str, t.Any]:
        filtered_res = {
            "ansible_facts": task_result.get("ansible_facts", {}),
            "warnings": task_result.get("warnings", []),
            "deprecations": task_result.get("deprecations", []),
        }

        # on conflict the last plugin processed wins, but try to do deep merge and append to lists.
        return merge_hash(result, filtered_res, list_merge="append_rp")

    def run(self, tmp=None, task_vars=None):
        if task_vars is None:
            task_vars = {}

        result = super(ActionModule, self).run(tmp, task_vars)
        result.update(
            {
                "changed": False,
                "warnings": [],
                "instances": [],  # This is from resolved platform instances
                "nodes": [],  # This is after converging with cluster state
                "results": [],  # This is returned to molecule to write out
            },
        )

        validation_result, new_module_args = self.validate_argument_spec(
            argument_spec={
                "proxmox_conf": {"type": "dict", "default": {}},
                "proxmox_instances": {"type": "list", "required": True},
                "verbosity": {"type": "int", "default": 0},
                "state": {
                    "type": "str",
                    "default": "started",
                    "choices": ["absent", "started"],
                },
            },
        )

        proxmox_conf = new_module_args["proxmox_conf"]
        proxmox_instances = new_module_args["proxmox_instances"]
        module_state = new_module_args["state"]

        start = utcnow()
        display.display("start: %s" % str(start))

        #
        # Attempt to handle some timeout cases
        #
        max_timeout = self._connection._play_context.timeout
        task_poll = self._task.poll
        task_async = self._task.async_val
        check_mode = self._play_context.check_mode

        display.display(
            "max_timeout: {max_timeout}, task_poll: {task_poll},  task_async: {task_async}, check_mode: {check_mode}, self._task.async_val: {async_val} , self._task.task_action: {task_action} , self._task.timeout: {timeout} ".format(
                max_timeout=max_timeout,
                task_poll=task_poll,
                task_async=task_async,
                check_mode=check_mode,
                async_val=self._task.async_val,
                task_action=self._task.action,
                timeout=self._task.timeout,
            ),
        )

        function_from_module_utils()

        lookup = self._templar.copy_with_new_env(
            globals=task_vars,
        )._lookup

        if module_state == "absent":
            node_action = LxcNodeAction.DESTROY
        else:
            node_action = LxcNodeAction.CREATE

        if not proxmox_instances:
            result["warnings"] = (
                "operating against no instances, usually is a config problem"
            )
            result["changed"] = False
            return result

        _common_module_args = {
            "api_host": proxmox_conf["api_host"],
            # "api_port": proxmox_conf["api_port"],  # noqa: ERA001
            "api_user": proxmox_conf["api_user"],
            "api_password": proxmox_conf["api_password"],
            "api_token_id": proxmox_conf["api_token_id"],
            "api_token_secret": proxmox_conf["api_token_secret"],
            "node": proxmox_conf["node"],
            "validate_certs": False,
        }

        # get initial cluster state
        cluster_info = get_cluster_info(
            self,
            start,
            _common_module_args,
            task_vars,
            tmp,
        )

        node_dict = {}
        for instance in proxmox_instances:
            node = LxcNode.from_cluster_info(
                cluster_info=cluster_info,
                instance=instance,
                action=node_action,
                lookup=lookup,
            )
            node_dict[instance["proxmox_hostname"]] = node

        syslog.syslog("nodes is {}".format(node_dict))

        count = 0
        while any(node.state != LxcNodeStatus.COMPLETE for node in node_dict.values()):
            count = count + 1
            if count > 175:
                raise AnsibleActionFail(f"Too loopy for me {count}")
            job_list = {
                node.proxmox_hostname: node.get_job({})
                for node in node_dict.values()
                if node.state != LxcNodeStatus.COMPLETE
            }
            job_results = AnsibleAsyncExecutor.async_executor(
                self,
                start,
                job_list,
                task_vars,
                _common_module_args,
            )
            for proxmox_hostname, job_result in job_results.items():
                logger.info(
                    json.dumps(job_result),
                    extra={
                        "stream-token": "molecule-proxmox-lxc",
                        "proxmox_hostname": proxmox_hostname,
                    },
                )
            for proxmox_hostname, job_result in job_results.items():
                node_dict[proxmox_hostname].update(job_result)

        if any(node.error for node in node_dict.values()):
            result["failed"] = True

        for node in node_dict.values():
            syslog.syslog("node is {}".format(node.name))
            result["nodes"].append(str(node))
            if node.proxmox_exists:
                result["results"].append(node.to_instance_config())

        # this is a sanity check, should never happen
        if node_action == LxcNodeAction.CREATE:
            for _result in result["results"]:
                display.display(f"result: {_result}")
                if not _result["vmid"] or not _result["address"]:
                    _result["failed"] = True
                    result["failed"] = True
                    result["warnings"].append(
                        "Failed to get vmid or address for instance {}".format(
                            _result["name"],
                        ),
                    )
            if len(result["results"]) == 0:
                result["failed"] = True
                result["warnings"].append("No instances found in cluster")

        result.pop("_vms_by_name", None)
        result.pop("_vms_by_id", None)
        return result
