"""Settings, from the environment (systemd reads /opt/vfg/.env into it)."""

import os
from collections import namedtuple

Config = namedtuple("Config", "db feed watch state console_log bind port client_token"
                              " base mc kit incantations owner live_poll_s cast_gap_s cast_slack_s"
                              " cast_wait_s incant_allowance incant_layer2 incant_layer2_max_words"
                              " jev_cast_max_age_s engine_socket engine_export engine_cast_wait_s")

DEFAULTS = {
    "VFG_DB": "/opt/vfg/vfg.db",
    "VFG_FEED": "/opt/vfg/feed.log",
    "VFG_WATCH": "/opt/vfg/watch.json",
    "VFG_STATE": "/opt/vfg/state",
    "VFG_CONSOLE_LOG": "/opt/mc/console.log",
    "VFG_BRIDGE_BIND": "0.0.0.0",
    "VFG_BRIDGE_PORT": "8787",
    "VFG_BASE": "/opt/vfg",
    "VFG_MC": "/opt/mc",
    "VFG_LIVE_POLL_S": "0.5",
    "VFG_CAST_GAP_S": "1.0",
    "VFG_CAST_SLACK_S": "0.3",
    "VFG_CAST_WAIT_S": "1.5",
    "VFG_INCANT_ALLOWANCE": "0.25",
    "VFG_INCANT_LAYER2": "on",
    "VFG_INCANT_LAYER2_MAX_WORDS": "6",
    "VFG_JEV_CAST_MAX_AGE_S": "2.0",
    "VFG_ENGINE_CAST_WAIT_S": "1.0",
}
OFF = ("0", "off", "false", "no")


def load(environ=None):
    env = os.environ if environ is None else environ

    def get(name):
        return env.get(name) or DEFAULTS[name]

    def number(name):
        value = get(name)
        try:
            value = float(value)
        except ValueError:
            raise SystemExit("vfg-bridge: %s is not a number: %r" % (name, value))
        if not 0 <= value < float("inf"):
            raise SystemExit("vfg-bridge: %s must be 0 or more: %r" % (name, value))
        return value

    def whole(name):
        value = get(name)
        try:
            value = int(value)
        except ValueError:
            raise SystemExit("vfg-bridge: %s is not a whole number: %r" % (name, value))
        if value < 0:
            raise SystemExit("vfg-bridge: %s must be 0 or more: %r" % (name, value))
        return value

    port = get("VFG_BRIDGE_PORT")
    try:
        port = int(port)
    except ValueError:
        raise SystemExit("vfg-bridge: VFG_BRIDGE_PORT is not a number: %r" % port)
    base = get("VFG_BASE")
    return Config(
        db=get("VFG_DB"),
        feed=get("VFG_FEED"),
        watch=get("VFG_WATCH"),
        state=get("VFG_STATE"),
        console_log=get("VFG_CONSOLE_LOG"),
        bind=get("VFG_BRIDGE_BIND"),
        port=port,
        client_token=(env.get("VFG_CLIENT_TOKEN") or "").strip(),
        # The box a cast starts its copy on (instances.Box), the kit the bridge imports
        # incantations and instances from, and the incantation store, as the kit finds it.
        base=base,
        mc=get("VFG_MC"),
        kit=env.get("VFG_KIT") or os.path.join(base, "scripts", "_lib"),
        incantations=env.get("VFG_INCANTATIONS") or os.path.join(base, "incantations.json"),
        owner=(env.get("VFG_OWNER") or "").strip(),
        live_poll_s=number("VFG_LIVE_POLL_S"),
        cast_gap_s=number("VFG_CAST_GAP_S"),
        cast_slack_s=number("VFG_CAST_SLACK_S"),
        cast_wait_s=number("VFG_CAST_WAIT_S"),
        incant_allowance=number("VFG_INCANT_ALLOWANCE"),
        incant_layer2=get("VFG_INCANT_LAYER2").strip().lower() not in OFF,
        incant_layer2_max_words=whole("VFG_INCANT_LAYER2_MAX_WORDS"),
        jev_cast_max_age_s=number("VFG_JEV_CAST_MAX_AGE_S"),
        # The game engine (casts.py, engine.py): its control socket, the export of the
        # incantations bound to its abilities, and how long a cast waits for its answer.
        engine_socket=env.get("VFG_ENGINE_SOCKET") or os.path.join(base, "engine", "engine.sock"),
        engine_export=env.get("VFG_ENGINE_EXPORT") or os.path.join(base, "engine", "incantations.json"),
        engine_cast_wait_s=number("VFG_ENGINE_CAST_WAIT_S"),
    )
