from molecule import api


def test_proxmox_lxc_driver_is_detected():
    assert "molecule-proxmox-lxc" in [str(d) for d in api.drivers()]
