#!/usr/bin/python
# Make coding more python3-ish, this is required for contributions to Ansible


import os
import re
import time
import typing as t
from collections import defaultdict
from enum import Enum
from io import StringIO

from ansible.utils.display import Display
from colorama import Back, Fore, Style
from rich.console import Console

display = Display()
import syslog  # noqa: E402

from ansible.errors import (
    AnsibleActionFail,
)
from ansible.plugins.action import ActionBase
from ansible.utils.vars import merge_hash


def rich_to_ansi(data: dict) -> str:
    # Create a StringIO buffer to capture Rich output
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True)

    # Render the dictionary to the console
    console.print(data)

    # Get the ANSI-encoded string
    return buffer.getvalue()


class LxcNodeStatus(Enum):
    """
    This is the internal state of the node, set to drive the next action on the node
    """

    INITIAL = 0
    STOPPED = 1
    NO_NET = 4
    COMPLETE = 5
    STOP = 6


class LxcNodeAction(Enum):
    """
    This is derived from the state set of the ansible module when invoked
    is created as enum on node, so don't have to pass it around
    """

    CREATE = 1
    DESTROY = 2


class LxcNode:
    def __init__(
        self,
        name,
        proxmox_hostname,
        instance,
        vmid=None,
        template=None,
        status=None,
        address=None,
        action=LxcNodeAction.CREATE,
        state=LxcNodeStatus.INITIAL,
        changed=False,
        proxmox_exists=None,
    ):
        self.name = name  # name is molecule instance name
        self.proxmox_hostname = proxmox_hostname
        self.instance = instance
        self.vmid = vmid
        self.status = status  # This is proxmox running, started, stopped etc
        self._proxmox_exists = proxmox_exists
        self.template = template
        self.address = address
        self.create_args = {}
        self.error = None
        self.errors = []
        self._state = state  # This is the internal tracking state
        self.action = action  # This corresponds to the action we want to take
        self.changed = changed  # Value ultimately returned to Ansible
        self._failed = False
        self.invocation = None

    @staticmethod
    def from_cluster_info(vm_info, instance, action, proxmox_exists):
        return LxcNode(
            name=instance["name"],
            proxmox_hostname=vm_info["name"],
            proxmox_exists=proxmox_exists,
            vmid=vm_info["vmid"],
            instance=instance,
            status=vm_info.get("status"),
            template=vm_info.get("template"),
            action=action,
        )

    def __str__(self):
        return "name: {} hostname: {} vmid: {} status: {} template: {} address: {} state: {} error: {} proxmox_exists: {} invocation: {}".format(
            self.name,
            self.proxmox_hostname,
            self.vmid,
            self.status,
            self.template,
            self.address,
            self._state,
            self.error,
            self.proxmox_exists,
            self.invocation,
        )

    def to_instance_config(self):
        res = self.instance.copy()
        res.update(
            {
                "proxmox_hostname": self.proxmox_hostname,
                "address": self.address,
                "identity_file": self.instance["identity_file"],
                "port": self.instance["host_port"],
                "user": self.instance["host_user"],
                "status": self.status,
                "vmid": self.vmid,
                "changed": self.changed,
                "failed": self.failed,
            },
        )
        if self.failed:
            res["errors"] = self.errors
            res["invocation"] = self.invocation
            res["error"] = self.error
        return res

    def __repr__(self):
        return self.__str__()

    @property
    def failed(self):
        return self._failed

    @failed.setter
    def failed(self, value):
        if value != self._failed:
            display.display(f"Setting failed from {self._failed} to {value}")
            self._failed = value

    @property
    def proxmox_exists(self):
        return self._proxmox_exists

    @proxmox_exists.setter
    def proxmox_exists(self, value):
        if value == self._proxmox_exists:
            return
        display.display(
            f"Setting proxmox_exists from {self._proxmox_exists} to {value}",
        )
        self._proxmox_exists = value

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value == self._state:
            return
        display.display(
            "Setting state from {} to {}".format(
                Back.LIGHTMAGENTA_EX + str(self._state) + Style.RESET_ALL,
                Back.BLUE + str(value) + Style.RESET_ALL,
            ),
        )
        self._state = value

    def update(self, job_result):
        try:
            if job_result.get("state", False) not in ["failed", "skipped"]:
                # self.error = job_result["data"]
                # self.state = LxcNodeStatus.COMPLETE
                # return
                if job_result["type"] == "community.general.proxmox_vm_info":
                    _vms = job_result["data"]["proxmox_vms"]
                    if len(_vms) == 1:
                        data = job_result["data"]["proxmox_vms"][0]
                        data.pop("name", None)
                    elif len(_vms) == 0:
                        data = job_result["data"]
                    elif len(_vms) > 1:
                        raise AnsibleActionFail(
                            "Multiple VMs with query {} found, provide vmid instead".format(
                                self.name,
                            ),
                        )
                    else:
                        data = job_result["data"]

                    if "status" in data:
                        self.proxmox_exists = True
                else:
                    data = job_result.get("data")
            else:
                data = job_result.get("data")
        except Exception as e:
            display.display(f"failed to get data from job_result {job_result}")
            display.display(f"Exception: {e}")
            display.display(f"node: {self}")
            raise

        self.show_state("Pre update", data, job_result)

        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

        # reset failed state
        self.failed = data.get("failed", False)

        try:
            if data.get("network") and isinstance(data["network"], list):
                if len(data["network"]) > 1:
                    for net in data["network"]:
                        if net["name"] == self.instance["netifname"] and "inet" in net:
                            self.address = net["inet"].split("/")[0]
        except:
            pass

        # handler various failures states whether to retry or not
        if job_result.get("state") == "failed":
            if data.get("msg"):
                if "Timeout exceeded" in data["msg"]:
                    pass  # try again
                elif "all of the following are missing" in data["msg"] or re.search(
                    "^Creation of lxc VM.*failed", data["msg"]
                ):
                    self.failed = True
                    self.error = data["msg"]
                    self.errors.append(data["msg"])
                    self.state = LxcNodeStatus.COMPLETE
                    self.invocation = data.get("invocation")
                else:
                    self.handle_unknown_update(data, job_result)
            else:
                self.handle_unknown_update(data, job_result)
        elif self.action == LxcNodeAction.CREATE:
            if self.vmid:
                display.display("action with vmid")
                if data.get("msg") and "is already running" in data["msg"]:
                    self.state = LxcNodeStatus.NO_NET
                elif data.get("msg") and "CT is locked (disk)" in data["msg"]:
                    self.state = LxcNodeStatus.INITIAL
                elif self.address and self.status == "running":
                    self.state = LxcNodeStatus.COMPLETE
                elif not self.status:
                    self.state = LxcNodeStatus.NO_NET
                elif self.status == "stopped":
                    self.state = LxcNodeStatus.STOPPED
                elif self.status == "running" and not self.address:
                    self.state = LxcNodeStatus.NO_NET
                else:
                    self.handle_unknown_update(data, job_result)
            else:
                if self.status == "stopped":
                    self.state = LxcNodeStatus.STOPPED
                elif (
                    data.get("msg")
                    and "already exists and has ID number" in data["msg"]
                ):
                    self.vmid = data["vmid"]
                    self.state = LxcNodeStatus.STOPPED
                elif self.status == "running" and not self.address:
                    self.state = LxcNodeStatus.NO_NET
                else:
                    self.handle_unknown_update(data, job_result)
        elif self.action == LxcNodeAction.DESTROY:
            if self.vmid:
                display.display("DESTROY action with vmid")
                if data.get("msg") and "is shutting down" in data["msg"]:
                    self.state = LxcNodeStatus.NO_NET
                elif data.get("msg") and "does not exist" in data["msg"]:
                    self.proxmox_exists = False
                    self.state = LxcNodeStatus.COMPLETE
                elif data.get("msg") and "is already shutdown" in data["msg"]:
                    self.state = LxcNodeStatus.STOPPED
                elif self.status == "running":
                    self.state = LxcNodeStatus.STOP
                elif self.status == "stopped":
                    self.state = LxcNodeStatus.STOPPED
                else:
                    self.handle_unknown_update(data, job_result)
            else:
                if "proxmox_vms" in data and len(data["proxmox_vms"]) == 0:
                    self.state = LxcNodeStatus.COMPLETE
                else:
                    self.handle_unknown_update(data, job_result)

        else:
            self.handle_unknown_update(data, job_result)

        self.show_state("After update", data, job_result)

        # elif self.action == LxcNodeAction.DESTROY:

    def show_state(self, msg, data, job_result):
        display.display(
            """{}{}{} for node '{}' type {}
vmid    '{}'
type    '{}'
state   '{}'
status  '{}'
action  '{}'
msg     '{}'
exist   '{}'
t_state '{}'
             """.format(
                Back.LIGHTBLUE_EX,
                msg,
                Style.RESET_ALL,
                Back.LIGHTMAGENTA_EX + self.name + Style.RESET_ALL,
                job_result.get("type"),
                Back.LIGHTYELLOW_EX + str(self.vmid) + Style.RESET_ALL,
                job_result["type"],
                Back.LIGHTCYAN_EX + str(self.state) + Style.RESET_ALL,
                Back.LIGHTRED_EX + str(self.status) + Style.RESET_ALL,
                Back.YELLOW + str(self.action) + Style.RESET_ALL,
                Back.LIGHTGREEN_EX + str(data.get("msg")) + Style.RESET_ALL,
                Back.LIGHTBLUE_EX + str(self.proxmox_exists) + Style.RESET_ALL,
                Back.LIGHTBLUE_EX + str(job_result.get("state")) + Style.RESET_ALL,
            ),
        )

    def handle_unknown_update(self, data, job_result):
        display.display(
            "{} failed to process update {}\n {}\n".format(
                Fore.RED,
                Style.RESET_ALL,
                rich_to_ansi(job_result),
            ),
        )
        display.display(f"node: {self}")
        display.display(f"job_result {job_result}")
        self.state = LxcNodeStatus.COMPLETE

    def get_job(self, extra_args):
        if self.state == LxcNodeStatus.INITIAL:
            if self.action == LxcNodeAction.CREATE:
                result = {
                    "name": self.name,
                    "description": f"Sending create job for {self.name} ({self.vmid})",
                    "mod_args": self.create_args,
                    "module_name": "community.general.proxmox",
                }
                result["mod_args"].update(extra_args)
                return result
            elif self.action == LxcNodeAction.DESTROY:
                if self.vmid and self.status == "stopped":
                    result = {
                        "name": self.name,
                        "description": "Sending destroy job for {} ({})".format(
                            self.name,
                            self.vmid,
                        ),
                        "mod_args": {
                            "vmid": self.vmid,
                            "state": "absent",
                            "timeout": 120,
                        },
                        "module_name": "community.general.proxmox",
                    }
                elif self.vmid and self.status == "running":
                    result = {
                        "name": self.name,
                        "description": f"Sending stop job for {self.name} ({self.vmid})",
                        "mod_args": {
                            "vmid": self.vmid,
                            "state": "stopped",
                            "timeout": 120,
                        },
                        "module_name": "community.general.proxmox",
                    }
                else:
                    result = {
                        "name": self.name,
                        "data_key": "proxmox_vms",
                        "description": "Request vm_info job for {} ({})".format(
                            self.name,
                            str(self.vmid),
                        ),
                        "mod_args": {
                            "type": "lxc",
                            "name": self.proxmox_hostname,
                            "network": True,
                        },
                        "module_name": "community.general.proxmox_vm_info",
                    }
                result["mod_args"].update(extra_args)
                return result
        elif self.state == LxcNodeStatus.STOPPED:
            if self.action == LxcNodeAction.CREATE:
                result = {
                    "name": self.name,
                    "description": f"Sending start job for {self.name} ({self.vmid})",
                    "mod_args": {
                        "vmid": self.vmid,
                        "state": "started",
                        "timeout": 120,
                    },
                    "module_name": "community.general.proxmox",
                }
                result["mod_args"].update(extra_args)
                return result
            elif self.action == LxcNodeAction.DESTROY:
                result = {
                    "name": self.name,
                    "description": f"Sending destroy job for {self.name} ({self.vmid})",
                    "mod_args": {
                        "vmid": self.vmid,
                        "state": "absent",
                        "timeout": 120,
                    },
                    "module_name": "community.general.proxmox",
                }
                result["mod_args"].update(extra_args)
                return result
        elif self.state == LxcNodeStatus.NO_NET:
            result = {
                "name": self.name,
                "data_key": "proxmox_vms",
                "description": f"Request vm_info job for {self.name} ({self.vmid})",
                "mod_args": {
                    "type": "lxc",
                    "vmid": self.vmid,
                    "network": True,
                },
                "module_name": "community.general.proxmox_vm_info",
            }
            result["mod_args"].update(extra_args)
            return result
        elif self.state == LxcNodeStatus.STOP:
            result = {
                "name": self.name,
                "description": f"Sending a stop job for {self.name} ({self.vmid})",
                "mod_args": {
                    "vmid": self.vmid,
                    "state": "stopped",
                    "timeout": 120,
                },
                "module_name": "community.general.proxmox",
            }
            result["mod_args"].update(extra_args)
            return result
        else:
            return


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

    def async_executor(self, job_list, task_vars=None):
        job_results = defaultdict(dict)

        jobs = {}
        for key, job in job_list.items():
            # Add async parameters to module args
            async_args = job["mod_args"].copy()
            # async_args["_async_timeout"] = 600  # 10 minutes timeout
            # async_args["_async"] = True
            # result["instances"].append(instance)
            # display._display.vv("Running queue job")
            if "description" in job:
                display.display("Running queue: '{}'".format(job["description"]))
            jobs[key] = self._execute_module(
                module_name=job["module_name"],
                module_args=async_args,
                task_vars=job["task_vars"] if "task_vars" in job else task_vars,
                wrap_async=True,
            )

        while jobs:
            for module in jobs:
                poll_args = {
                    "jid": jobs[module]["ansible_job_id"],
                    "_async_dir": os.path.dirname(jobs[module]["results_file"]),
                }
                res = self._execute_module(
                    module_name="ansible.legacy.async_status",
                    module_args=poll_args,
                    task_vars=task_vars,
                    wrap_async=False,
                )
                syslog.syslog(f"res {res}")
                if res.get("finished", 0) == 1:
                    if res.get("failed", False):
                        job_results[module]["state"] = "failed"
                    elif res.get("skipped", False):
                        job_results[module]["state"] = "skipped"
                    else:
                        job_results[module]["state"] = "finished"
                        job_results[module]["key"] = module
                    job_results[module]["type"] = job_list[module]["module_name"]
                    job_results[module]["data"] = res
                    del jobs[module]
                    break
                else:
                    time.sleep(2)
            else:
                time.sleep(2)

        return job_results

    def get_cluster_info(self, module_args, task_vars, tmp):
        module_return = self._execute_module(
            module_name="community.general.proxmox_vm_info",
            module_args=module_args,
            task_vars=task_vars,
            tmp=tmp,
        )
        vms_by_name_counts = defaultdict(list)
        vms_by_name = {}
        for item in module_return["proxmox_vms"]:
            vms_by_name_counts[item["name"]].append(item)
        for k, v in vms_by_name_counts.items():
            if len(v) == 1:
                vms_by_name[k] = v[0]
        vms_by_id = {item["vmid"]: item for item in module_return["proxmox_vms"]}
        if len(vms_by_name) != len(vms_by_id):
            display.warning(
                "Found non-unique VM names in Proxmox cluster ({})".format(
                    str(
                        [
                            f"{vm}:{len(vms)}"
                            for vm, vms in vms_by_name_counts.items()
                            if len(vms) > 1
                        ],
                    ),
                ),
            )
        return vms_by_name, vms_by_id

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
            task_vars = dict()

        lookup = self._templar.copy_with_new_env(globals=task_vars)._lookup

        validation_result, new_module_args = self.validate_argument_spec(
            argument_spec={
                "proxmox_conf": {"type": "dict", "default": {}},
                "proxmox_instances": {"type": "list", "required": True},
                "verbosity": {"type": "int", "default": 0},
                "state": dict(
                    type="str",
                    default="started",
                    choices=["absent", "started"],
                ),
            },
        )

        proxmox_conf = new_module_args["proxmox_conf"]
        proxmox_instances = new_module_args["proxmox_instances"]
        module_state = new_module_args["state"]

        if module_state == "absent":
            node_action = LxcNodeAction.DESTROY
        else:
            node_action = LxcNodeAction.CREATE

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

        display.display("debug something")

        if not proxmox_instances:
            result["warnings"] = (
                "operating against no instances, usually is a config problem"
            )
            result["changed"] = False
            return result

        _common_module_args = {
            "api_host": proxmox_conf["api_host"],
            "api_port": proxmox_conf["api_port"],
            "api_user": proxmox_conf["api_user"],
            "api_password": proxmox_conf["api_password"],
            "api_token_id": proxmox_conf["api_token_id"],
            "api_token_secret": proxmox_conf["api_token_secret"],
            "node": proxmox_conf["node"],
            "validate_certs": False,
        }

        # get initial cluster state
        vms_by_name, vms_by_id = self.get_cluster_info(
            _common_module_args,
            task_vars,
            tmp,
        )
        result["_vms_by_name"] = vms_by_name
        result["_vms_by_id"] = vms_by_id

        # syslog.syslog("cluster_info {}".format(result["vms_by_id"]))

        node_dict = {}
        for instance in proxmox_instances:
            # These are the 2 normal cases. Either present or absent
            # initialize any matching instances - already present in cluster
            # This is typically the molecule destroy case
            if instance.get("vmid", False) and instance["vmid"] in vms_by_id:
                node = LxcNode.from_cluster_info(
                    vm_info=result["_vms_by_id"][instance["vmid"]],
                    instance=instance,
                    action=node_action,
                    proxmox_exists=True,
                )
            # This is typically the molecule create case
            # instance config from molecule.yml and not in cluster
            elif (
                not instance.get("vmid", False)
                and instance["proxmox_hostname"] not in vms_by_name
            ):
                node = LxcNode(
                    name=instance["name"],
                    proxmox_hostname=instance["proxmox_hostname"],
                    instance=instance,
                    action=node_action,
                    proxmox_exists=False,
                )
            # we have vmid but it doesn't exist in remote
            elif instance.get("vmid", False):
                raise AnsibleActionFail(
                    "Have vmid that doesn't exist in cluster. If you have deleted the instance manually, use `molecule reset` to clear your local state.\n  instance: ({})".format(
                        instance.get("proxmox_hostname")
                    ),
                )
            elif (
                instance.get("proxmox_hostname", False)
                and instance["proxmox_hostname"] in vms_by_name
            ):
                raise AnsibleActionFail(
                    "Found VM with hostname {} which we don't know about. Cannot continue.\n  instance: ({})".format(
                        instance["proxmox_hostname"],
                        instance,
                    ),
                )
            else:
                node = LxcNode(
                    name=instance["name"],
                    proxmox_hostname=instance["proxmox_hostname"],
                    instance=instance,
                    action=node_action,
                )
            node_dict[node.name] = node

        display.display(
            f"Initialized node_dict from vm_info: {len(node_dict)} ",
        )
        for k, v in node_dict.items():
            display.display(
                f"node: {k} status: {v.status} proxmox_hostname: {v.proxmox_hostname}",
            )

        for instance in proxmox_instances:
            if any(key in instance for key in ["clone_from_name", "clone_from_id"]):
                if "clone_from_id" in instance:
                    if (
                        instance.get("clone_from_id")
                        and instance["clone_from_id"] in vms_by_id
                    ):
                        clone_vmid = instance["clone_from_id"]
                else:
                    clone_vm = vms_by_name.get(instance["clone_from_name"])
                    if clone_vm:
                        node_dict[instance["name"]].create_args.update(
                            {
                                "clone": clone_vm["vmid"],
                                "clone_type": instance["clone_type"],
                                "storage": "",
                                "hostname": instance["proxmox_hostname"],
                                "timeout": 180,
                                "state": "present",
                                "description": "Managed by molecule-proxmox-lxc",
                            },
                        )
                    else:
                        raise AnsibleActionFail(
                            "Unable to find clone from target for {} for {}".format(
                                instance["clone_from_name"],
                                instance,
                            ),
                        )
            elif "ostemplate" in instance:
                display.display("processing full instance {}".format(instance["name"]))
                # module = module_loader.get("community.general.proxmox")
                # arg_spec = module.argument_spec
                arg_spec = [
                    "ostemplate",
                    "storage",
                    "cpus",
                    "cpuunits",
                    "cores",
                    "memory",
                    "swap",
                    "netif",
                    "onboot",
                    "disk",
                    "features",
                    "tags",
                    "hookscript",
                ]
                valid_args = {k: v for k, v in instance.items() if k in arg_spec}
                node_dict[instance["name"]].create_args.update(valid_args)
                node_dict[instance["name"]].create_args.update(
                    {
                        "hostname": instance["proxmox_hostname"],
                        "password": instance["host_password"],
                        "timeout": 180,
                        "state": "present",
                        "description": "Managed by molecule-proxmox-lxc",
                        "pubkey": lookup(
                            "ansible.builtin.file",
                            instance["identity_file"] + ".pub",
                        ),
                    },
                )
            else:
                raise AnsibleActionFail(f"Arg parsing failure for {instance}")
            node_dict[instance["name"]].create_args.update(_common_module_args)

        syslog.syslog(f"nodes is {node_dict}")
        count = 0
        while any(node.state != LxcNodeStatus.COMPLETE for node in node_dict.values()):
            count = count + 1
            if count > 75:
                raise AnsibleActionFail(f"Too loopy for me {count}")
            job_list = {
                node.name: node.get_job(_common_module_args)
                for node in node_dict.values()
                if node.state != LxcNodeStatus.COMPLETE
            }
            job_results = self.async_executor(job_list, task_vars)
            for key, job_result in job_results.items():
                node_dict[key].update(job_result)

        if any(node.error for node in node_dict.values()):
            result["failed"] = True

        for key, node in node_dict.items():
            display.display(f"adding {node.name} ")
            syslog.syslog(f"node is {node.name}")
            result["nodes"].append(str(node))
            if node.proxmox_exists:
                result["results"].append(node.to_instance_config())

        # this is a sanity check, should never happen
        for _result in result["results"]:
            display.display(f"result: {_result}")
            if node_action == LxcNodeAction.CREATE:
                if not _result["vmid"] or not _result["address"]:
                    _result["failed"] = True
                    result["failed"] = True
                    result["warnings"].append(
                        "Failed to get vmid or address for instance {}".format(
                            _result["name"],
                        ),
                    )

        if node_action == LxcNodeAction.CREATE:
            if len(result["results"]) == 0:
                result["failed"] = True
                result["warnings"].append("No instances found in cluster")

        result.pop("_vms_by_name", None)
        result.pop("_vms_by_id", None)
        return result
