"""The implementers' settings, from the environment and ~/.vfg/config; the price table their usage
is priced by; and the log line format."""
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

HERE = os.path.dirname(os.path.abspath(__file__))  # brain/brain/implementers
BRAIN = os.path.dirname(os.path.dirname(HERE))  # brain/
ROOT = os.path.dirname(BRAIN)  # the repo this brain/ is in


def expand_home(p: str) -> str:
    """`~` and `~/…` only, as the shell would; never `~user`."""
    if p == "~":
        return os.path.expanduser("~")
    if p.startswith("~/"):
        return os.path.join(os.path.expanduser("~"), p[2:])
    return p


def _real(p: str) -> str:
    path = os.path.abspath(expand_home(p))
    try:
        return os.path.realpath(path) if os.path.exists(path) else path
    except OSError:
        return path


_NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def js_number(v: str | None) -> float:
    """A string as JavaScript's Number() reads it, for the forms that matter here: NaN when it
    is not a plain decimal number, and 0 for an empty one."""
    if v is None:
        return math.nan
    s = v.strip()
    if s == "":
        return 0.0
    return float(s) if _NUMBER_RE.match(s) else math.nan


def _int(v: str | None, default: int, minimum: int = 1) -> int:
    n = math.nan if v is None or v == "" else js_number(v)
    return math.floor(n) if math.isfinite(n) and n >= minimum else default


_SERVER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
CACHE_TTLS = ("5m", "1h")


def vfg_config(env: Mapping[str, str]) -> dict[str, str]:
    """$VFG_HOME/config (~/.vfg/config), KEY=value lines, as toolkit/vfg reads it; {} when unreadable."""
    path = os.path.join(expand_home(env.get("VFG_HOME") or "~/.vfg"), "config")
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                out[key] = value
    except OSError:
        pass
    return out


def vfg_setting(env: Mapping[str, str], file: Mapping[str, str], key: str) -> str:
    """A VFG_* setting the way vfg takes it: the environment, else ~/.vfg/config; "" for the default."""
    return env.get(key) or file.get(key) or ""


def _seconds_setting(v: str, default: float, lo: float, hi: float) -> float:
    n = js_number(v) if v else math.nan
    return n if math.isfinite(n) and lo <= n <= hi else default


# The providers the Claude Code adapter reaches the model at (harness/__init__.py has the harnesses).
PROVIDERS = ("deepseek", "openrouter", "anthropic")
# Each provider's model and Claude-compatible address, when VFG_IMPL_MODEL and VFG_IMPL_BASE_URL do
# not say. The anthropic provider has no address: the CLI's own, on the owner's subscription login.
MODELS = {"deepseek": "deepseek-flash", "openrouter": "deepseek/deepseek-v4.1-flash", "anthropic": "claude-haiku-5-5"}
BASE_URLS = {"deepseek": "https://api.deepseek.com/anthropic", "openrouter": "https://openrouter.ai/api", "anthropic": ""}
# OpenRouter's providers for DeepSeek V4.1 Flash, by slug, tried in this order, fallbacks allowed:
# Together first, for speed (Jesse, 2026-10-13). Together turns high into DeepSeek's 50, DigitalOcean
# and Ionstream into 75 (EFFORTS). The scenario eval of 2026-10-13 (the same 6 scenarios, 2 rounds
# each, on Together): high did 46 of 54 items against off's 44, within run-to-run noise, with about
# half the requests and fewer failed tests of its own. A run at 75 on DigitalOcean was about 50%
# slower, and with no run of off on DigitalOcean nothing is owed to 75 over 50. Each keeps its own
# prompt cache, so a run that moves between them misses it. Measured on six4: DigitalOcean 99.1% of
# input from the cache, Ionstream 98.7%, Together 98.4%; Morph 78%, Relace 80%, Venice 55.7%.
OR_ORDER = ("together", "digitalocean", "ionstream")
OR_IGNORE = ("venice", "morph", "relace")

# VFG_IMPL_EFFORT, how hard the model thinks, one setting for every provider and harness: off sends
# thinking disabled, as before 2026-10-13; low, high and max are sent as the effort (relay.py,
# opencode.py; on anthropic, the CLI's own --effort). DeepSeek V4.1 Flash takes low, high and max,
# 50, 75 and 100 on its 1–100 effort scale, high when none is set (its encoding/encoding.py); a
# medium is taken here as high, as DeepSeek reads one, and sent as high, since through OpenRouter a
# medium reached Together as 25. Which number a word reaches depends on the host, measured on
# 2026-10-13 through OpenRouter's Claude-compatible API (the model reading the number off its own
# prompt, "Reasoning Effort: N"): DigitalOcean and Ionstream take DeepSeek's own scale, low, high and
# max at 50, 75 and 100, none set at 75; Together takes the open-weights encoder's (vLLM's), low,
# high and max at 25, 50 and 100, none set at 50, and an xhigh reached it as high, 50, so no word
# reaches 75 there. OpenRouter passes no number through.
EFFORTS = ("off", "low", "high", "max")
EFFORT_DEFAULT = "high"
EFFORT_ALIASES = {"medium": "high"}


def read_effort(v: str) -> str:
    """VFG_IMPL_EFFORT as the implementers take it: off, low, high or max; "" for high; medium is
    high. Anything else is a ValueError that says so: a mistyped effort never runs as another."""
    word = (v or "").strip().lower()
    if not word:
        return EFFORT_DEFAULT
    word = EFFORT_ALIASES.get(word, word)
    if word not in EFFORTS:
        raise ValueError("VFG_IMPL_EFFORT=%s is none of %s (medium is read as high)" % (v.strip(), ", ".join(EFFORTS)))
    return word


# How long a harness's shell tool lets one command run when the model sets no timeout: 10 minutes,
# what a `vfg test` may take in all (its wait behind other implementers' tests, VFG_TEST_QUEUE_S,
# 300 s; up to two waits for the test server to come back, VFG_TEST_BOOT_S, 120 s each; the test,
# VFG_TEST_TIMEOUT_S, 60 s by default), never more than the run's own limit; and the most the model
# may set, the run's limit, which stops the run anyway. The harnesses' own default is 2 minutes,
# which would cut a test short.
BASH_DEFAULT_S = 600.0


def bash_timeouts_ms(cfg: "ImplementerConfig") -> tuple[int, int]:
    """(the shell tool's default, its most), in milliseconds, for this run's limit."""
    return int(min(BASH_DEFAULT_S, cfg.time_limit_s) * 1000), int(cfg.time_limit_s * 1000)


# VFG_TEST_BOX's word for the live box's own test server, its default (vfg test lays it beside the
# live server: the live folders + "-test", its own RCON port); "none" says there is no test server.
TEST_BOX_DEFAULT = "box"


def read_test_box(v: str) -> str:
    """VFG_TEST_BOX as the implementers take it: unset or empty, "box" (the live box's own test
    server); "none", "" (no test server); anything else as given (a test server's settings file)."""
    word = (v or "").strip()
    if not word:
        return TEST_BOX_DEFAULT
    return "" if word.lower() == "none" else word


def slugs(v: str, default: tuple[str, ...]) -> list[str]:
    """A comma-separated list of provider slugs; "" for the default, "none" for none."""
    if not v.strip():
        return list(default)
    if v.strip().lower() == "none":
        return []
    return [x.strip().lower() for x in v.split(",") if x.strip()]


def provider_routing(cfg: "ImplementerConfig") -> dict | None:
    """OpenRouter's `provider` object for every request on the openrouter provider: our order of the
    providers (OR_ORDER), those that cache badly left out, and fallbacks allowed, so an outage of
    the ordered ones does not fail a run. None on any other provider."""
    if cfg.provider != "openrouter":
        return None
    out: dict = {"allow_fallbacks": True}
    if cfg.or_order:
        out["order"] = list(cfg.or_order)
    if cfg.or_ignore:
        out["ignore"] = list(cfg.or_ignore)
    return out


def openrouter_key(env: Mapping[str, str], file: Mapping[str, str], home: str) -> str:
    """The OpenRouter key as vfg_llm reads it: OPEN_ROUTER_API, else alone in the file
    VFG_OPENROUTER_KEY names (the environment, then ~/.vfg/config), else ~/.vfg/openrouter.key; ""
    when there is none."""
    key = (env.get("OPEN_ROUTER_API") or "").strip()
    if key:
        return key
    path = expand_home(env.get("VFG_OPENROUTER_KEY") or file.get("VFG_OPENROUTER_KEY")
                       or os.path.join(home, "openrouter.key"))
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


@dataclass
class ImplementerConfig:
    # The implementers' working directory, which holds engine/ and made/, resolved: VFG_REPO, else
    # the repo this brain/ is in.
    repo: str
    # vfg's own folder, VFG_HOME (~/.vfg): its config file, and the brain's folder by default.
    home: str
    # The brain's folder, VFG_BRAIN_DIR (VFG_HOME/brain): py_compile's cache and the implementer's
    # system prompt file go in it.
    brain_dir: str
    # brain/prompts/implementer.md: the implementer's rules.
    prompt: str
    # The harness (VFG_IMPL_HARNESS), the provider it reaches the model at (VFG_IMPL_PROVIDER:
    # deepseek, openrouter or anthropic; unset, deepseek when DEEPSEEK_API_KEY is set, else
    # openrouter), the model (VFG_IMPL_MODEL, before it VFG_IMPLEMENTER_MODEL; the provider's own
    # by default) and the provider's Claude-compatible address (VFG_IMPL_BASE_URL). The effort
    # (VFG_IMPL_EFFORT: off, low, high or max; high by default) is the model's thinking on every
    # provider and harness (EFFORTS, above).
    harness: str = "claude-code"
    provider: str = "openrouter"
    model: str = MODELS["openrouter"]
    base_url: str = BASE_URLS["openrouter"]
    effort: str = EFFORT_DEFAULT
    # The provider's key: DEEPSEEK_API_KEY for deepseek; for openrouter, OPEN_ROUTER_API or the key
    # file (openrouter_key); none for anthropic, the owner's login. Never printed, logged or written.
    key: str = field(default="", repr=False)
    # Turns before the CLI stops it (VFG_IMPL_MAX_TURNS), and how long one may run before it is
    # stopped and reported failed (VFG_IMPLEMENTER_MINUTES, 20: thinking on took 3.5 times as long
    # as off on 2026-10-11, and 3.5 times the longest run of the night of 2026-10-12, 277 s with it
    # off, is 16 minutes).
    max_turns: int = 100
    time_limit_s: float = 1200.0
    # vfg's settings, read as vfg reads them (vfg_setting): the server whose made/<server>/content/
    # implementers write in, how long vfg route may take, and the prompt cache's life on the
    # anthropic provider (VFG_IMPL_CACHE_TTL, before it VFG_SUBAGENT_CACHE_TTL).
    server: str = "main"
    route_timeout_s: float = 3.0
    cache_ttl: str = "5m"
    # On OpenRouter, the providers tried first, in order, and those never used (VFG_IMPL_OR_ORDER,
    # VFG_IMPL_OR_IGNORE: comma-separated slugs; "none" for none), for the prompt cache.
    or_order: list = field(default_factory=lambda: list(OR_ORDER))
    or_ignore: list = field(default_factory=lambda: list(OR_IGNORE))
    # The test server vfg test plays on (VFG_TEST_BOX, read_test_box): "box", the live box's own,
    # by default; a settings file; "" when there is none ("none"). With one, a run's look at the live
    # world is budgeted (guard.probe_reason).
    test_box: str = TEST_BOX_DEFAULT

    @classmethod
    def load(cls, env: Mapping[str, str] | None = None) -> "ImplementerConfig":
        if env is None:
            env = os.environ
        file = vfg_config(env)
        server = vfg_setting(env, file, "VFG_SERVER")
        ttl = vfg_setting(env, file, "VFG_IMPL_CACHE_TTL") or vfg_setting(env, file, "VFG_SUBAGENT_CACHE_TTL")
        home = os.path.abspath(expand_home(env.get("VFG_HOME") or "~/.vfg"))
        harness = vfg_setting(env, file, "VFG_IMPL_HARNESS") or "claude-code"
        provider = vfg_setting(env, file, "VFG_IMPL_PROVIDER")
        if provider not in PROVIDERS:
            if provider:
                log("config", "VFG_IMPL_PROVIDER=%s is none of %s: the default is taken" % (provider, ", ".join(PROVIDERS)))
            provider = "deepseek" if (env.get("DEEPSEEK_API_KEY") or "").strip() else "openrouter"
        key = ((env.get("DEEPSEEK_API_KEY") or "").strip() if provider == "deepseek"
               else openrouter_key(env, file, home) if provider == "openrouter" else "")
        return cls(
            repo=_real(expand_home(vfg_setting(env, file, "VFG_REPO")) or ROOT),
            home=home,
            brain_dir=os.path.abspath(expand_home(vfg_setting(env, file, "VFG_BRAIN_DIR") or os.path.join(home, "brain"))),
            prompt=os.path.join(BRAIN, "prompts", "implementer.md"),
            harness=harness,
            provider=provider,
            model=(vfg_setting(env, file, "VFG_IMPL_MODEL") or vfg_setting(env, file, "VFG_IMPLEMENTER_MODEL")
                   or MODELS[provider]),
            base_url=(vfg_setting(env, file, "VFG_IMPL_BASE_URL") or BASE_URLS[provider]).rstrip("/"),
            effort=read_effort(vfg_setting(env, file, "VFG_IMPL_EFFORT")),
            key=key,
            max_turns=_int(vfg_setting(env, file, "VFG_IMPL_MAX_TURNS"), 100),
            time_limit_s=60.0 * _int(vfg_setting(env, file, "VFG_IMPLEMENTER_MINUTES"), 20),
            # A name vfg would refuse is never joined into a path here; vfg itself says what is wrong.
            server=server if _SERVER_RE.match(server) else "main",
            route_timeout_s=_seconds_setting(vfg_setting(env, file, "VFG_ROUTE_TIMEOUT_S"), 3.0, 0.1, 60),
            cache_ttl=ttl if ttl in CACHE_TTLS else "5m",
            or_order=slugs(vfg_setting(env, file, "VFG_IMPL_OR_ORDER"), OR_ORDER),
            or_ignore=slugs(vfg_setting(env, file, "VFG_IMPL_OR_IGNORE"), OR_IGNORE),
            test_box=read_test_box(vfg_setting(env, file, "VFG_TEST_BOX")),
        )


# ------------------------------------------------------------------ the price table

@dataclass(frozen=True)
class Price:
    """Dollars per million tokens: an input token the cache did not have (a cache miss), one read
    from the cache (a hit), one written to it, and an output token."""
    input: float
    hit: float
    write: float
    output: float

    def times(self, k: float) -> "Price":
        return Price(self.input * k, self.hit * k, self.write * k, self.output * k)


@dataclass(frozen=True)
class ModelPrice:
    """One model's prices at one provider. `off_peak` is the factor on every price outside the
    provider's peak hours (None: one price at every hour); a request of more than `step_at` tokens
    costs `step` times every price for the whole request (Haiku's long-context price); `context` is
    how many tokens the model reads, which the Claude Code adapter sets its auto-compact window to."""
    base: Price
    off_peak: float | None = None
    step_at: int | None = None
    step: float = 1.0
    context: int | None = None


# DeepSeek's peak, by the request's UTC hour, Monday to Friday: 01:00–04:00 and 06:00–10:00.
DEEPSEEK_PEAK_HOURS = frozenset([1, 2, 3, 6, 7, 8, 9])
# DeepSeek V4.1 Flash at its peak price. DeepSeek has no price for writing the cache: a miss is input.
_DEEPSEEK_FLASH = Price(input=0.30, hit=0.006, write=0.30, output=1.20)

PRICES: dict[str, ModelPrice] = {
    # On DeepSeek's own API: half price off-peak.
    "deepseek-flash": ModelPrice(_DEEPSEEK_FLASH, off_peak=0.5, context=1_000_000),
    # On OpenRouter, which charges its provider's price at every hour: the peak price always.
    "deepseek/deepseek-v4.1-flash": ModelPrice(_DEEPSEEK_FLASH, context=1_000_000),
    # Haiku 5.5: five times every price for the whole request when it is over 100K tokens.
    "claude-haiku-5-5": ModelPrice(Price(input=0.10, hit=0.01, write=0.125, output=0.50), step_at=100_000, step=5.0),
}


def price_key(model: str) -> str:
    """A model's name as the price table has it: lower case, without a context suffix (`[1m]`)."""
    return re.sub(r"\[[^\]]*\]$", "", (model or "").strip()).lower()


def model_price(model: str) -> ModelPrice | None:
    return PRICES.get(price_key(model))


def peak(at_s: float) -> bool:
    """Is `at_s` (seconds since the epoch) in DeepSeek's peak hours?"""
    d = datetime.fromtimestamp(at_s, timezone.utc)
    return d.weekday() < 5 and d.hour in DEEPSEEK_PEAK_HOURS


def price_at(model: str, size: int, at_s: float) -> Price | None:
    """The prices one request of `size` tokens (input, cache read and cache write together) pays,
    sent at `at_s`; None for a model the table does not have."""
    m = model_price(model)
    if m is None:
        return None
    p = m.base
    if m.off_peak is not None and not peak(at_s):
        p = p.times(m.off_peak)
    if m.step_at is not None and size > m.step_at:
        p = p.times(m.step)
    return p


_quiet = False


def set_quiet(q: bool) -> None:
    """Tests silence the log."""
    global _quiet
    _quiet = q


def _part(p) -> str:
    if isinstance(p, str):
        return p
    if isinstance(p, BaseException):
        return str(p)
    return json.dumps(p, ensure_ascii=False, separators=(",", ":"), default=str)


def log(area: str, *parts) -> None:
    """One log line on stdout: `<ISO time> <area> <message>`, as the brain's own, into brain.log."""
    if _quiet:
        return
    msg = " ".join(_part(p) for p in parts).replace("\n", " ")
    sys.stdout.write("%s %s %s\n" % (iso(now_ms()), area, msg))
    sys.stdout.flush()


def now_ms() -> float:
    """Milliseconds since the epoch."""
    return datetime.now(timezone.utc).timestamp() * 1000


def iso(ms: float) -> str:
    """2026-10-07T18:22:31.412Z: milliseconds, UTC, as every timestamp in the log."""
    d = datetime.fromtimestamp(math.floor(ms) / 1000, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def js_num(x: float) -> str:
    """A number as JavaScript prints it in a template string: 2, 0.5, never 2.0."""
    return str(int(x)) if float(x).is_integer() else repr(float(x))
