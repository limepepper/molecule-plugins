import os
import platform
from importlib.util import find_spec

import pytest

from conftest import change_dir_to
from molecule import logger, scenarios, util
from molecule.command import base
from molecule.util import run_command
from molecule_plugins.proxmox_lxc.playbooks.module_utils.molecule_proxmox_lxc import (
    generate_proxmox_hostname,
)

LOG = logger.get_logger(__name__)

HAS_PROXMOXER = bool(find_spec("proxmoxer"))


def is_proxmox_lxc_available() -> bool:
    """
    Can we run real tests here?
    """
    if platform.machine() == "arm64" and platform.system() == "Darwin":
        return False
    if not HAS_PROXMOXER:
        return False
    return os.environ.get("PROXMOX_API_HOST", False)


def _get_proxmox_api(api):
    return api.proxmox_api.cluster().resources().get(type="vm")


@pytest.mark.proxmox_lxc
@pytest.mark.skipif(
    not is_proxmox_lxc_available(),
    reason="Proxmox Lxc not supported on this machine",
)
@pytest.mark.parametrize(
    "scenario",
    [
        {
            "name": "default",
            "instance_count": 2,
        },
        {
            "name": "linked_clones",
            "instance_count": 8,
        },
        {
            "name": "various_disks",
            "instance_count": 3,
        },
    ],
)
def test_proxmox_lxc_scenarios(temp_dir, proxmox_api, scenario):
    scenario_directorys = os.path.join(
        os.path.dirname(util.abs_path(__file__)),
        "scenarios",
    )

    with change_dir_to(scenario_directorys):
        args = {"debug": False, "base_config": []}
        command_args = {"subcommand": "list", "format": "yaml"}

        s = scenarios.Scenarios(
            base.get_configs(args, command_args),
            scenario["name"],
        )
        status = next(s).config.driver.status()

        assert len(status) == scenario["instance_count"]
        assert all(s.created == "false" for s in status)
        assert all(s.converged == "false" for s in status)

        vms = proxmox_api.cluster().resources().get(type="vm")
        for s in status:
            proxmox_hostname = generate_proxmox_hostname(
                s.scenario_name,
                s.instance_name,
            )
            vm = [vm for vm in vms if vm["name"] == proxmox_hostname]
            if len(vm) > 0:
                LOG.info("querying for proxmox_hostname: %s", proxmox_hostname)
                LOG.info("vm: %s", vm)

            assert vm == []

        # assert False

        cmd = ["molecule", "-v", "destroy", "--scenario-name", scenario["name"]]
        result = run_command(
            cmd,
        )
        assert result.returncode == 0

        cmd = ["molecule", "-v", "create", "--scenario-name", scenario["name"]]
        result = run_command(
            cmd,
        )
        assert result.returncode == 0

        cmd = ["molecule", "-v", "converge", "--scenario-name", scenario["name"]]
        result = run_command(
            cmd,
        )
        assert result.returncode == 0

        vms = proxmox_api.cluster().resources().get(type="vm")
        for s in status:
            proxmox_hostname = generate_proxmox_hostname(
                s.scenario_name,
                s.instance_name,
            )
            vm = [vm for vm in vms if vm["name"] == proxmox_hostname]
            if len(vm) != 1:
                LOG.warning("querying for proxmox_hostname: %s", proxmox_hostname)
                LOG.warning("vm: %s", vm)
            assert len(vm) == 1
            assert vm[0]

        cmd = ["molecule", "-v", "verify", "--scenario-name", scenario["name"]]
        result = run_command(
            cmd,
        )
        assert result.returncode == 0

        cmd = ["molecule", "-v", "destroy", "--scenario-name", scenario["name"]]
        result = run_command(
            cmd,
        )
        assert result.returncode == 0

        vms = proxmox_api.cluster().resources().get(type="vm")
        for s in status:
            proxmox_hostname = generate_proxmox_hostname(
                s.scenario_name,
                s.instance_name,
            )
            vm = [vm for vm in vms if vm["name"] == proxmox_hostname]
            vm = [vm for vm in vms if vm["name"] == proxmox_hostname]
            if len(vm) > 0:
                LOG.info("querying for proxmox_hostname: %s", proxmox_hostname)
                LOG.info("vm: %s", vm)

            assert vm == []
