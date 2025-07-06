import os

import pytest
from packaging.version import Version

PROXMOXER_IMP_ERR = None
try:
    from proxmoxer import ProxmoxAPI
    from proxmoxer import __version__ as proxmoxer_version

    HAS_PROXMOXER = True
except ImportError:
    HAS_PROXMOXER = False


@pytest.fixture
def proxmox_api():
    api_host = os.environ.get("PROXMOX_API_HOST")
    api_port = os.environ.get("PROXMOX_API_PORT")
    api_user = os.environ.get("PROXMOX_API_USER")
    api_password = os.environ.get("PROXMOX_API_PASSWORD")
    api_token_id = os.environ.get("PROXMOX_API_TOKEN_ID")
    api_token_secret = os.environ.get("PROXMOX_API_TOKEN_SECRET")
    validate_certs = os.environ.get("PROXMOX_API_VERIFY_SSL", False)

    auth_args = {"user": api_user}

    if api_port:
        auth_args["port"] = api_port

    if api_password:
        auth_args["password"] = api_password
    else:
        if Version(proxmoxer_version) < Version("1.1.0"):
            msg = "proxmox token requires proxmoxer >= 1.1.0"
            raise ValueError(msg)

        auth_args["token_name"] = api_token_id
        auth_args["token_value"] = api_token_secret

    return ProxmoxAPI(api_host, verify_ssl=validate_certs, **auth_args)
