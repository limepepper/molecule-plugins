import os
import platform

import pytest

from conftest import change_dir_to
from molecule import logger, util
from molecule.util import run_command

LOG = logger.get_logger(__name__)


def is_proxmox_lxc_available() -> bool:
    """Return True if vagrant is installed and current platform is supported."""
    if not os.environ.get("PROXMOX_API_HOST"):
        return False
    if platform.machine() == "arm64" and platform.system() == "Darwin":
        return False
    return True


@pytest.mark.optional
@pytest.mark.skipif(
    not is_proxmox_lxc_available(),
    reason="Proxmox Lxc not supported on this machine",
)
@pytest.mark.parametrize(
    "scenario",
    [
        ("linked_clones"),
    ],
)
def test_proxmox_lxc_root(temp_dir, scenario):
    scenario_directory = os.path.join(
        os.path.dirname(util.abs_path(__file__)),
        "scenarios",
    )

    with change_dir_to(scenario_directory):
        cmd = ["molecule", "-v", "test", "--scenario-name", scenario]
        env = os.environ.copy()
        env["ANSIBLE_VERBOSITY"] = "3"
        result = run_command(
            cmd,
            env=env,
        )
        assert result.returncode == 0
