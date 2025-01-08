import os
import platform
from importlib.util import find_spec

import pytest
from ansible_collections.community.general.plugins.module_utils.proxmox import (
    ProxmoxAnsible,
)

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
    """Return True if vagrant is installed and current platform is supported."""
    if not os.environ.get("PROXMOX_API_HOST"):
        return False
    if not HAS_PROXMOXER:
        return False
    return not (platform.machine() == "arm64" and platform.system() == "Darwin")


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
            "name": "linked_clones",
            "instance_count": 8,
        },
        {
            "name": "various_disks",
            "instance_count": 3,
        },
    ],
)
def test_proxmox_lxc_scenarios(temp_dir, module, scenario):
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

        api = ProxmoxAnsible(module)
        api.proxmox_api.cluster().resources().get(type="vm")
        for s in status:
            proxmox_hostname = generate_proxmox_hostname(
                s.scenario_name,
                s.instance_name,
            )
            vm = api.get_vmid(proxmox_hostname, ignore_missing=True)
            if vm is not None:
                LOG.info("querying for proxmox_hostname: %s", proxmox_hostname)
                LOG.info("vm: %s", vm)
                api.proxmox_api.cluster().resources().get()

            assert vm is None

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

        for s in status:
            proxmox_hostname = generate_proxmox_hostname(
                s.scenario_name,
                s.instance_name,
            )
            vm = api.get_vmid(proxmox_hostname, ignore_missing=True)
            if vm is None:
                LOG.warning("querying for proxmox_hostname: %s", proxmox_hostname)
                LOG.warning("vm: %s", vm)
            assert vm is not None

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

        for s in status:
            proxmox_hostname = generate_proxmox_hostname(
                s.scenario_name,
                s.instance_name,
            )
            vm = api.get_vmid(proxmox_hostname, ignore_missing=True)
            if vm is not None:
                LOG.warning("querying for proxmox_hostname: %s", proxmox_hostname)
                LOG.warning("vm: %s", vm)
            assert vm is None
