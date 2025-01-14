# ruff: noqa: UP032, G001
import json
import logging
import re
from collections import defaultdict
from enum import Enum
from io import StringIO

from ansible.errors import (
    AnsibleActionFail,
)
from ansible.utils.display import Display
from colorama import Back, Fore, Style
from rich.console import Console

from molecule_plugins.proxmox_lxc.playbooks.module_utils.async_executor import (
    AnsibleAsyncExecutor,
)

logger = logging.getLogger("proxmox_lxc")
display = Display()


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
    START = 7


class LxcNodeAction(Enum):
    """
    This is derived from the state set of the ansible module when invoked
    is created as enum on node, so don't have to pass it around
    """

    CREATE = 1
    DESTROY = 2


def generate_proxmox_hostname(
    scenario_name,
    instance_name,
    project_name=None,
):
    if project_name:
        base_name = "{}-{}-{}".format(project_name, scenario_name, instance_name)
    else:
        base_name = "{}-{}".format(scenario_name, instance_name)
    return sanitize_hostname(base_name)


def sanitize_hostname(hostname):
    """Replace any invalid characters in a DNS hostname."""
    # Replace invalid characters with a hyphen
    res = re.sub(r"[^a-zA-Z0-9-.]", "-", hostname)
    res = res.lower()
    return res


def next_vmid(all_vmids, min_idx=100, max_idx=10000):
    """
    find first available int from 100 that is not in the cluster vmids list
    """
    idx = min_idx
    while idx < max_idx:
        if idx not in all_vmids:
            all_vmids.append(idx)
            return idx
        idx = idx + 1
    msg = "Could not find unique vmid in set {}".format(str(all_vmids))
    raise ValueError(msg)


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
        cluster_info=None,
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
        self.update_job_args = {}
        self.error = None
        self.errors = []
        self._state = state  # This is the internal tracking state
        self.action = action  # This corresponds to the action we want to take
        self.changed = changed  # Value ultimately returned to Ansible
        self._failed = False
        self.invocation = None
        self.cluster_info = cluster_info

    @classmethod
    def from_cluster_info(
        cls,
        cluster_info,
        instance,
        action,
        lookup,
    ):
        """
        Get and parse the output of the proxmox_vm_info module.

        In proxmox there is no requirement to have unique "hostname" attribute,
        internally it's tracking by vmid. However, from our point of view we don't know
        any vmid until we have created nodes or done a vm_info query. Hence, we want
        to determine if any duplicate names are going to cause a problem later.

        :param cluster_info:
        :param instance:
        :param action:
        :param lookup:
        :return:
        """
        vm_by_id = cluster_info["vms_by_id"].get(instance.get("vmid"))
        vm_by_name = cluster_info["vms_by_name"].get(instance["proxmox_hostname"])
        all_vmids = cluster_info["all_vmids"]
        dupe_names = cluster_info["dupe_names"]

        if instance["proxmox_hostname"] in dupe_names:
            msg = "Multiple vms exist with name {} in remote cluster".format(
                instance["proxmox_hostname"],
            )
            raise AnsibleActionFail(msg)

        # These are the 2 normal cases. Either present or absent. i.e. clean start/stop
        # in the destroy case, instance has vmid and pve_hostname, which exist in the
        # cluster_info in both vm by name and id, and the node they refer to matches
        if (
            vm_by_id
            and vm_by_name
            and vm_by_id["name"] == vm_by_name["name"]
            and vm_by_id["vmid"] == vm_by_name["vmid"]
        ):
            logger.debug("found matching proxmox_hostname and vmid")
            vmid = instance.get("vmid")
            status = vm_by_id.get("status", "unknown")
            proxmox_exists = True  # To distinguish generated vmid. Maybe not use this
            template = vm_by_id.get("template")
        # In the "create" case, the local instance does not have vmid info, and the
        # proxmox_hostname does not exist in the cluster info.
        elif not (vm_by_id or vm_by_name):
            logger.debug("initializing new instance with new vmid")
            vmid = next_vmid(all_vmids)
            proxmox_exists = False
            status = None
            template = None

        # we have vmid but it doesn't exist in remote
        elif instance.get("vmid") and not vm_by_id:
            msg = "Have vmid that doesn't exist in cluster. If you have deleted the instance manually, use `molecule reset` to clear your local state.\n  instance: ({})".format(
                instance.get("proxmox_hostname"),
            )
            raise AnsibleActionFail(msg)
        # This is the failed 'create' case. We found the name, but
        # no record of it in molecule config
        elif not instance.get("vmid") and vm_by_name:
            msg = "Found VM with hostname {} which we don't know about.\n  instance: ({})".format(
                instance["proxmox_hostname"],
                instance,
            )
            display.warning(msg)
            vmid = vm_by_name["vmid"]
            status = vm_by_name.get("status", "unknown")
            proxmox_exists = True
            template = vm_by_name.get("template")
        else:
            msg = "Unknown problem. unable to proceed.\n hostname: {} instance: ({})".format(
                instance["proxmox_hostname"],
                instance,
            )
            raise AnsibleActionFail(msg)
        node = LxcNode(
            name=instance["name"],
            proxmox_hostname=instance["proxmox_hostname"],
            vmid=vmid,
            instance=instance,
            status=status,
            template=template,
            action=action,
            proxmox_exists=proxmox_exists,
            cluster_info=cluster_info,
        )

        node.get_create_jobs(lookup)
        node.get_update_job(lookup)
        return node

    def __str__(self):
        return f"name: {self.name} hostname: {self.proxmox_hostname} vmid: {self.vmid} status: {self.status} template: {self.template} address: {self.address} state: {self._state} error: {self.error} proxmox_exists: {self.proxmox_exists} invocation: {self.invocation}"

    def get_update_job(self, lookup):
        display.display("updating clone instance {}".format(self.instance["name"]))
        arg_spec = [
            "cores",
            "cpus",
            "cpuunits",
            "ip_address",
            "memory",
            "memory",
            "nameserver",
            "netif",
            "onboot",
            "ostype",
            "searchdomain",
            "startup",
            "swap",
            "tags",
            "timezone",
        ]
        valid_args = {k: v for k, v in self.instance.items() if k in arg_spec}
        self.update_job_args.update(valid_args)
        self.update_job_args.update(
            {
                "hostname": self.proxmox_hostname,
                "vmid": self.vmid,
                "update": True,
            },
        )

    def get_create_jobs(self, lookup):
        if any(key in self.instance for key in ["clone_from_name", "clone_from_id"]):
            if "clone_from_id" in self.instance:
                clone_vm = self.cluster_info["vms_by_id"].get(
                    self.instance["clone_from_id"],
                )
            else:
                clone_vm = self.cluster_info["vms_by_name"].get(
                    self.instance["clone_from_name"],
                )
            if clone_vm:
                self.create_args.update(
                    {
                        "clone": clone_vm["vmid"],
                        "clone_type": self.instance["clone_type"],
                        "hostname": self.instance["proxmox_hostname"],
                        "vmid": self.vmid,
                        "timeout": 10,
                        "state": "present",
                        "description": "Managed by molecule-proxmox-lxc",
                    },
                )
            else:
                msg = "Unable to find clone from target for {} for {}".format(
                    clone_vm,
                    self.instance,
                )
                raise AnsibleActionFail(msg)
        elif "ostemplate" in self.instance:
            logger.debug("processing full instance {}".format(self.instance["name"]))
            arg_spec = [
                "cores",
                "cpus",
                "cpuunits",
                "disk",
                "features",
                "hookscript",
                "ip_address",
                "memory",
                "memory",
                "nameserver",
                "netif",
                "onboot",
                "ostemplate",
                "ostype",
                "pool",
                "searchdomain",
                "startup",
                "storage",
                "swap",
                "tags",
                "timezone",
            ]
            valid_args = {k: v for k, v in self.instance.items() if k in arg_spec}
            self.create_args.update(valid_args)
            self.create_args.update(
                {
                    "hostname": self.proxmox_hostname,
                    "password": self.instance["host_password"],
                    "timeout": 10,
                    "state": "present",
                    "vmid": self.vmid,
                    "description": "Managed by molecule-proxmox-lxc",
                    "pubkey": lookup(
                        "ansible.builtin.file",
                        self.instance["identity_file"] + ".pub",
                    ),
                },
            )
        else:
            raise AnsibleActionFail(f"Arg parsing failure for {self.instance}")

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
            logger.debug(
                "Setting failed from %s to %s",
                self._failed,
                value,
            )
            self._failed = value

    @property
    def proxmox_exists(self):
        return self._proxmox_exists

    @proxmox_exists.setter
    def proxmox_exists(self, value):
        if value == self._proxmox_exists:
            return
        logger.debug(
            "Setting proxmox_exists from %s to %s",
            self._proxmox_exists,
            value,
        )
        self._proxmox_exists = value

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        if value == self._state:
            return
        logger.debug(
            "Setting state from %s to %s",
            Back.LIGHTMAGENTA_EX + str(self._state) + Style.RESET_ALL,
            Back.BLUE + str(value) + Style.RESET_ALL,
        )
        self._state = value

    # def handle_vm_info(self, vm_info):

    def update(self, job_result):
        self.show_state(Back.YELLOW + "Pre update" + Back.RESET)

        try:
            if job_result.get("state", False) not in ["failed", "skipped"]:
                if job_result["module_name"] == "community.general.proxmox_vm_info":
                    _vms = job_result["data"]["proxmox_vms"]
                    if len(_vms) == 1:
                        data = job_result["data"]["proxmox_vms"][0]
                        data.pop("name", None)
                    elif len(_vms) == 0:
                        data = job_result["data"]
                    elif len(_vms) > 1:
                        msg = "Multiple VMs with query {} found".format(
                            self.name,
                        )
                        raise AnsibleActionFail(msg)
                    else:
                        data = job_result["data"]

                    if "status" in data:
                        self.proxmox_exists = True
                else:
                    data = job_result.get("data")
            else:
                data = job_result.get("data")
        except Exception as e:
            display.error(
                "failed to get data from job_result {job_result}".format(
                    job_result=job_result,
                ),
            )
            display.error("Exception: {e}".format(e=e))
            display.error("node: {node}".format(node=self))
            raise

        self.show_update(job_result, data)

        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)

        # reset failed state
        self.failed = data.get("failed", False)

        try:
            if isinstance(data.get("network"), list) and len(data["network"]) > 1:
                for net in data["network"]:
                    if net["name"] == self.instance["netifname"] and "inet" in net:
                        self.address = net["inet"].split("/")[0]
        except Exception:
            pass

        # handler various failures states whether to retry or not
        if job_result.get("state") == "failed":
            if data.get("msg"):
                # if timeout, then the operation is in progress
                if (
                    "Timeout exceeded" in data["msg"]
                    or "Reached timeout while waiting for creating VM" in data["msg"]
                    or "Failed to retrieve LXC VMs information" in data["msg"]
                ):
                    # Need to get the vm_info again
                    self.state = LxcNodeStatus.NO_NET
                elif "CT is locked (disk)" in data.get("msg") and re.search(
                    "Cloning lxc VM .* failed",
                    data.get("msg"),
                ):
                    # This vmid that was selected for creation is invalid for new request
                    self.vmid = None
                    self.state = LxcNodeStatus.INITIAL
                elif "all of the following are missing" in data["msg"] or re.search(
                    "^Creation of lxc VM.*failed",
                    data["msg"],
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
        elif job_result.get("state") == "finished":
            if self.action == LxcNodeAction.CREATE:
                if self.vmid:
                    display.display("action with vmid")
                    if "is already running" in data.get("msg", ""):
                        self.state = LxcNodeStatus.NO_NET
                    elif re.search(
                        "VM with vmid = .* is already exists",
                        data.get(
                            "msg",
                            "",
                        ),
                    ):
                        self.failed = True
                        self.error = data["msg"]
                        self.errors.append(data["msg"])
                        self.state = LxcNodeStatus.COMPLETE
                    elif self.address and self.status == "running":
                        self.state = LxcNodeStatus.COMPLETE
                    elif not self.status:
                        self.state = LxcNodeStatus.NO_NET
                    elif self.status == "stopped" and re.search(
                        "Configured VM .*",
                        data.get("msg", ""),
                    ):
                        self.state = LxcNodeStatus.START
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
                    elif len(data.get("proxmox_vms", [])) == 0:
                        self.state = LxcNodeStatus.COMPLETE
                    else:
                        self.handle_unknown_update(data, job_result)
                else:
                    if "proxmox_vms" in data and len(data["proxmox_vms"]) == 0:
                        self.state = LxcNodeStatus.COMPLETE
                    else:
                        self.handle_unknown_update(data, job_result)
            else:
                self.handle_unknown_update(data, job_result)

        else:
            self.handle_unknown_update(data, job_result)

        self.show_state(Back.LIGHTBLUE_EX + "After update" + Back.RESET)

    def show_state(self, title):
        display.display(
            """{} for node '{}'
vmid    '{}'
state   '{}' in '{}'
status  '{}'
exist   '{}'
address '{}'
             """.format(
                title,
                Fore.LIGHTMAGENTA_EX + self.proxmox_hostname + Fore.RESET,
                Back.LIGHTYELLOW_EX + str(self.vmid) + Style.RESET_ALL,
                Back.LIGHTCYAN_EX + str(self.state) + Style.RESET_ALL,
                str(self.action),
                Back.LIGHTRED_EX + str(self.status) + Style.RESET_ALL,
                Back.LIGHTBLUE_EX + str(self.proxmox_exists) + Style.RESET_ALL,
                str(self.address),
            ),
        )

    def show_update(self, job_result, data):
        display.display(
            """processing update for node '{name}'
module   '{module_name}'
msg      '{msg}'
desc     '{description}'
state    '{state}'
mod_args '{mod_args}'
data     '{data}'
r_vmid   '{vmid}'
             """.format(
                name=Fore.LIGHTMAGENTA_EX + self.proxmox_hostname + Fore.RESET,
                module_name=job_result["module_name"],
                msg=Back.LIGHTGREEN_EX
                + str(job_result["data"].get("msg"))
                + Style.RESET_ALL,
                description=job_result.get("description", ""),
                mod_args=str(job_result.get("mod_args")),
                state=Back.LIGHTRED_EX
                + job_result.get("state", "UNKNOWN")
                + Back.RESET,
                data={k: v for k, v in data.items() if k not in ["msg", "invocation"]},
                vmid=job_result["data"].get("vmid", "UNKNOWN"),
            ),
        )

    def handle_unknown_update(self, data, job_result):
        display.display(
            f"{Fore.RED} failed to process update {Style.RESET_ALL}\n {rich_to_ansi(job_result)}\n",
        )
        display.display(f"node: {self}")
        display.display(f"job_result {job_result}")
        self.error = data.get("msg")
        self.errors.append(data.get("msg"))
        self.failed = True
        self.state = LxcNodeStatus.COMPLETE

    def get_job(self, extra_args):
        if self.state == LxcNodeStatus.INITIAL:
            if self.action == LxcNodeAction.CREATE:
                if self.proxmox_exists:
                    job = {
                        "name": self.name,
                        "data_key": "proxmox_vms",
                        "description": f"Request vm_info job for {self.proxmox_hostname} ({self.vmid!s})",
                        "mod_args": {
                            "type": "lxc",
                            "name": self.proxmox_hostname,
                            "vmid": self.vmid,
                            "network": True,
                        },
                        "module_name": "community.general.proxmox_vm_info",
                    }

                else:
                    job = {
                        "name": self.name,
                        "description": f"Sending create job for {self.proxmox_hostname} ({self.vmid})",
                        "mod_args": self.create_args,
                        "module_name": "community.general.proxmox",
                    }
                job["mod_args"].update(extra_args)
                return job
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
                        "description": f"Sending stop job for {self.proxmox_hostname} ({self.vmid})",
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
                        "description": f"Request vm_info job for {self.proxmox_hostname} ({self.vmid!s})",
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
                if self.instance.get("update"):
                    job = {
                        "name": self.name,
                        "description": f"Updating attributes for {self.proxmox_hostname} ({self.vmid})",
                        "module_name": "community.general.proxmox",
                        "mod_args": self.update_job_args,
                    }
                else:
                    job = {
                        "name": self.name,
                        "description": f"Sending start job for {self.proxmox_hostname} ({self.vmid})",
                        "module_name": "community.general.proxmox",
                        "mod_args": {
                            "vmid": self.vmid,
                            "state": "started",
                            "timeout": 120,
                        },
                    }
                    job["mod_args"].update(extra_args)
                return job
            elif self.action == LxcNodeAction.DESTROY:
                result = {
                    "name": self.name,
                    "description": f"Sending destroy job for {self.proxmox_hostname} ({self.vmid})",
                    "module_name": "community.general.proxmox",
                    "mod_args": {
                        "vmid": self.vmid,
                        "state": "absent",
                        "timeout": 120,
                    },
                }
                result["mod_args"].update(extra_args)
                return result
        elif self.state == LxcNodeStatus.START:
            job = {
                "name": self.name,
                "description": f"Sending start job for {self.proxmox_hostname} ({self.vmid})",
                "module_name": "community.general.proxmox",
                "mod_args": {
                    "vmid": self.vmid,
                    "state": "started",
                    "timeout": 120,
                },
            }
            job["mod_args"].update(extra_args)
            return job
        elif self.state == LxcNodeStatus.NO_NET:
            job = {
                "name": self.name,
                "data_key": "proxmox_vms",
                "description": f"Request vm_info job for {self.proxmox_hostname} ({self.vmid})",
                "module_name": "community.general.proxmox_vm_info",
                "mod_args": {
                    "type": "lxc",
                    "network": True,
                    "name": self.proxmox_hostname,
                    "vmid": self.vmid,
                },
            }

            job["mod_args"].update(extra_args)
            return job
        elif self.state == LxcNodeStatus.STOP:
            result = {
                "name": self.name,
                "description": f"Sending a stop job for {self.proxmox_hostname} ({self.vmid})",
                "module_name": "community.general.proxmox",
                "mod_args": {
                    "vmid": self.vmid,
                    "state": "stopped",
                    "timeout": 120,
                },
            }
            result["mod_args"].update(extra_args)
            return result
        else:
            return


def get_cluster_info(action_module, start, module_args, task_vars, tmp):
    """
    this utility method returns a dict of 1) a dict of vms with proxmox's name
    for the node as key, 2) a dict with the vmid as key, as 3) a list of used vmids
    :param start:
    :param module_args:
    :param task_vars:
    :param tmp:
    :return:
    """

    mod_args = module_args.copy()
    mod_args.update({"type": "lxc", "network": True})
    all_vmids_mod_args = module_args.copy()
    all_vmids_mod_args.update({"type": "all"})

    job_list = {
        "cluster_info": {
            "name": "cluster_info",
            "data_key": "proxmox_vms",
            "description": "Requesting cluster info",
            "mod_args": mod_args,
            "module_name": "community.general.proxmox_vm_info",
        },
        "all_vmids": {
            "name": "all_vmids",
            "data_key": "proxmox_vms",
            "description": "Requesting cluster vmid list",
            "mod_args": all_vmids_mod_args,
            "module_name": "community.general.proxmox_vm_info",
        },
    }
    job_results = AnsibleAsyncExecutor.async_executor(
        action_module,
        start,
        job_list,
        task_vars,
    )
    for job_result in job_results.values():
        logger.info(
            json.dumps(job_result),
            extra={"stream-token": "molecule-proxmox-lxc"},
        )
    all_vmids = [
        x.get("vmid")
        for x in job_results["all_vmids"]["data"]["proxmox_vms"]
        if x.get("vmid")
    ]
    module_return = job_results["cluster_info"]["data"]
    vms_by_name_counts = defaultdict(list)
    vms_by_name = {}
    dupe_names = []
    for item in module_return["proxmox_vms"]:
        vms_by_name_counts[item["name"]].append(item)
    for k, v in vms_by_name_counts.items():
        if len(v) == 1:
            vms_by_name[k] = v[0]
        if len(v) > 1:
            dupe_names.append(k)
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
    return {
        "vms_by_name": vms_by_name,
        "vms_by_id": vms_by_id,
        "all_vmids": all_vmids,
        "dupe_names": dupe_names,
    }
