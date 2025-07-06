#!/bin/bash
set -eu  # fail on unset or command error
set -o pipefail  # fail on commands in pipes

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

REPO_URL="https://github.com/rgl/proxmox-ve.git"
TARGET_DIR="$HOME/git/proxmox-ve"

sudo dnf install -y dnf-plugins-core
sudo dnf config-manager --add-repo https://rpm.releases.hashicorp.com/fedora/hashicorp.repo
sudo dnf install -y packer vagrant libvirt-devel

# <https://lunar.computer/news/vagrant-proxmox-60/>

# check if the vagrant-libvirt plugin is installed
if ! vagrant plugin list | grep '^vagrant-libvirt'; then
  vagrant plugin install vagrant-libvirt
fi

{
# check if the
if [ -d "$TARGET_DIR/.git" ]; then
    echo "Repository already exists. Updating..."
    cd "$TARGET_DIR" || exit
    git pull
else
    echo "Cloning repository..."
    git clone "$REPO_URL" "$TARGET_DIR"
fi
}

#
## make build-virtualbox
#make build-libvirt
#
#vagrant box add -f proxmox-ve-amd64 proxmox-ve-amd64-libvirt.box.json
#
## at this point I had to mess around with libvert in order to allow non root users to use it
#
#cd example/
#vagrant up --provider=libvirt
