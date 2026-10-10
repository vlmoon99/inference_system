#!/bin/bash
# Install + start the node watchdog as a user unit (the host dir is found by its .env):  deploy/install-unit.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p ~/.config/systemd/user
sed "s#%h/Documents/dev/inference_system#$(dirname "$here")#" "$here/inf-node.service" > ~/.config/systemd/user/inf-node.service
systemctl --user daemon-reload
systemctl --user enable --now inf-node.service
echo "inf-node.service enabled: journalctl --user -u inf-node -f"
