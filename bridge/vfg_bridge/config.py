"""Settings, from the environment (systemd reads /opt/vfg/.env into it)."""

import os
from collections import namedtuple

Config = namedtuple("Config", "db feed watch state console_log bind port client_token")

DEFAULTS = {
    "VFG_DB": "/opt/vfg/vfg.db",
    "VFG_FEED": "/opt/vfg/feed.log",
    "VFG_WATCH": "/opt/vfg/watch.json",
    "VFG_STATE": "/opt/vfg/state",
    "VFG_CONSOLE_LOG": "/opt/mc/console.log",
    "VFG_BRIDGE_BIND": "0.0.0.0",
    "VFG_BRIDGE_PORT": "8787",
}


def load(environ=None):
    env = os.environ if environ is None else environ

    def get(name):
        return env.get(name) or DEFAULTS[name]

    port = get("VFG_BRIDGE_PORT")
    try:
        port = int(port)
    except ValueError:
        raise SystemExit("vfg-bridge: VFG_BRIDGE_PORT is not a number: %r" % port)
    return Config(
        db=get("VFG_DB"),
        feed=get("VFG_FEED"),
        watch=get("VFG_WATCH"),
        state=get("VFG_STATE"),
        console_log=get("VFG_CONSOLE_LOG"),
        bind=get("VFG_BRIDGE_BIND"),
        port=port,
        client_token=(env.get("VFG_CLIENT_TOKEN") or "").strip(),
    )
