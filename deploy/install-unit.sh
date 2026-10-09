#!/bin/bash
# Install + enable the boot unit for this machine's host dir:  deploy/install-unit.sh dgx-spark
set -euo pipefail
host=${1:?host dir}
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p ~/.config/systemd/user
sed "s#%h/Documents/dev/inference_system#$(dirname "$here")#; s#HOSTDIR#$host#; s#(hosts/%I)#(hosts/$host)#" \
  "$here/inf-stack.service" > ~/.config/systemd/user/inf-stack.service
systemctl --user daemon-reload
systemctl --user enable inf-stack.service
echo "inf-stack.service enabled for hosts/$host"
