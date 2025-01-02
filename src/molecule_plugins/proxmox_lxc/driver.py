import os
from pathlib import Path

from molecule import logger, util
from molecule.api import Driver
from molecule_proxmox_lxc.data import __file__ as data_module

LOG = logger.get_logger(__name__)


class ProxmoxLxc(Driver):
    """
    The class responsible for managing Proxmox LXC instances.
    .. code-block:: yaml

        driver:
          name: proxmox-lxc
        platforms:
          - name: instance
            ostemplate: local:vztmpl/rockylinux-9-default_20240912_amd64.tar.xz
            memory: 1024
            cpus: 1
            disk: 16

    .. code-block:: bash

        $ python -m pip install -e 'git+https://github.com/limepepper/limepepper.common.git#egg=subdir&subdirectory='

    """  # noqa

    def __init__(self, config=None):
        super().__init__(config)
        self._name = "molecule-proxmox-lxc"

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def login_cmd_template(self):
        connection_options = " ".join(self.ssh_connection_options)
        return (
            "ssh {{address}} "
            "-l {{user}} "
            "-p {{port}} "
            "-i {{identity_file}} "
            "{}"
        ).format(connection_options)

    @property
    def default_safe_files(self):
        return [self.instance_config]

    @property
    def default_ssh_connection_options(self):
        return self._get_ssh_connection_options()

    def login_options(self, instance_name):
        d = {"instance": instance_name}
        return util.merge_dicts(d, self._get_instance_config(instance_name))

    def ansible_connection_options(self, instance_name):
        try:
            d = self._get_instance_config(instance_name)
            return {
                "ansible_user": d["user"],
                "ansible_host": d["address"],
                "ansible_port": d["port"],
                "ansible_private_key_file": d["identity_file"],
                "connection": "ssh",
                "ansible_ssh_common_args": " ".join(self.ssh_connection_options),  # noqa: E501
            }
        except StopIteration:
            return {}
        except IOError:
            # Instance has yet to be provisioned, therefore the
            # instance_config is not on disk.
            return {}

    def _get_instance_config(self, instance_name):
        instance_config_dict = util.safe_load_file(self._config.driver.instance_config)  # noqa: E501
        return next(
            item for item in instance_config_dict if item["instance"] == instance_name  # noqa: E501
        )

    def sanity_checks(self):
        pass

    def template_dir(self):
        """Return path to its own cookiecutterm templates. It is used by init
        command in order to figure out where to load the templates from.
        """
        return os.path.join(os.path.dirname(__file__), "cookiecutter")

    def modules_dir(self):
        return os.path.join(os.path.dirname(__file__), "modules")

    # def schema_file(self) -> str | None:
    #     return str(Path(data_module).parent / "driver.json")

