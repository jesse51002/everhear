#!/bin/sh
# Puts `vfg` on the PATH: ~/.local/bin/vfg -> toolkit/vfg. Run it again any time.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
vhome=${VFG_HOME:-$HOME/.vfg}

mkdir -p "$HOME/.local/bin" "$vhome/orch" "$vhome/ssh"
chmod 700 "$vhome" "$vhome/ssh"
chmod +x "$here/vfg" "$here/tests/fake_ssh" "$here/tests/fake_rcon.py" \
         "$here/tests/fake_vfg_feed.py" "$here/tests/bin/"*
ln -sfn "$here/vfg" "$HOME/.local/bin/vfg"
if [ ! -e "$vhome/config" ]; then
    cp "$here/config.example" "$vhome/config"
    chmod 600 "$vhome/config"
fi
echo "vfg installed — fill VFG_OWNER, VFG_SSH_HOST and VFG_SSH_KEY in ~/.vfg/config, then run vfg up"
