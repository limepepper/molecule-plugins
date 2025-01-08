import os
from unittest.mock import Mock

import pytest


@pytest.fixture
def module():
    attrs = {
        "method.fail_json": "some value",
        "params": {
            "api_host": os.environ.get("PROXMOX_API_HOST"),
            "api_user": os.environ.get("PROXMOX_API_USER"),
            "api_password": os.environ.get("PROXMOX_API_PASSWORD"),
            "api_token_id": os.environ.get("PROXMOX_API_TOKEN_ID"),
            "api_token_secret": os.environ.get("PROXMOX_API_TOKEN_SECRET"),
            "validate_certs": os.environ.get("PROXMOX_API_VERIFY_SSL", False),
            "node": os.environ.get("PROXMOX_API_NODE"),
        },
    }
    mock_module = Mock(some_attribute="eggs", **attrs)
    return mock_module
