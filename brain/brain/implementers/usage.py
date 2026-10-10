"""Per-model usage deltas from each result's modelUsage (the session's running totals,
subagents included) into `vfg usage add` rows."""
import collections
import functools
import math
from typing import Iterable, Literal

from .config import js_num, js_number, log
from .tasks import ModelTotals, Store
from .vfg import VfgRunner, why

Component = Literal["orchestrator", "implementer"]


def component_of(model: str, implementer_model: str = "sonnet") -> Component:
    """Which component a model's usage belongs to: the implementer's model (an alias such as
    "sonnet", or a full id) is the implementer; any other model is the orchestrator. Each row
    carries that model's own costUSD from the SDK, so it is priced as that model."""
    m = model.lower()
    impl = implementer_model.lower()
    return "implementer" if m == impl or impl in m else "orchestrator"


def _n(u: dict, key: str):
    v = u.get(key)
    return 0 if v is None else v


def totals_of(u: dict) -> ModelTotals:
    """One model's totals from the SDK's modelUsage entry (inputTokens, cacheReadInputTokens, …).
    "in" counts every input token; cacheRead and cacheWrite are the parts of it read from and
    written to the cache."""
    return {
        "in": _n(u, "inputTokens") + _n(u, "cacheReadInputTokens") + _n(u, "cacheCreationInputTokens"),
        "out": _n(u, "outputTokens"),
        "cost": _n(u, "costUSD"),
        "cacheRead": _n(u, "cacheReadInputTokens"),
        "cacheWrite": _n(u, "cacheCreationInputTokens"),
    }


def usage_deltas(snapshot: dict[str, ModelTotals], model_usage: dict[str, dict] | None, refs: list[str],
                 implementer_model: str = "sonnet") -> tuple[list[dict], dict[str, ModelTotals]]:
    """The rows for one result: for each model, its totals minus the snapshot; a total lower than
    the snapshot (a new session) counts from zero. Returns the rows and the new snapshot."""
    nxt = dict(snapshot)
    rows = []
    for model, u in (model_usage or {}).items():
        now = totals_of(u)
        zero = {"in": 0, "out": 0, "cost": 0, "cacheRead": 0, "cacheWrite": 0}
        prev = snapshot.get(model) or zero
        reset = (now["in"] < prev["in"] or now["out"] < prev["out"] or now["cost"] < prev["cost"] - 1e-9
                 or now["cacheRead"] < prev.get("cacheRead", 0) or now["cacheWrite"] < prev.get("cacheWrite", 0))
        base = zero if reset else prev
        d = {"in": now["in"] - base["in"], "out": now["out"] - base["out"], "cost": max(0, now["cost"] - base["cost"])}
        nxt[model] = now
        if d["in"] == 0 and d["out"] == 0:
            continue
        row = {"component": component_of(model, implementer_model), "model": model,
               "in": d["in"], "out": d["out"], "cost": d["cost"]}
        # A snapshot from before the cache was counted has no cacheRead: that row's share is unknown.
        if "cacheRead" in base and "cacheWrite" in base:
            row["cacheRead"] = now["cacheRead"] - base["cacheRead"]
            row["cacheWrite"] = now["cacheWrite"] - base["cacheWrite"]
        ref = join_refs(refs)
        if ref:
            row["ref"] = ref
        rows.append(row)
    return rows, nxt


# A call's cost in units of one fresh input token: Anthropic's ratios, which are Haiku 5.5's
# exactly (https://platform.claude.com/docs/en/about-claude/pricing, 2026-10-09: $0.10 input,
# $0.125 a 5-minute cache write, $0.20 a 1-hour one, $0.01 a cache read, $0.50 output, per
# million). A Haiku 5.5 call whose prompt, cache reads and writes included, is over LONG_PROMPT
# tokens pays five times as much for every token of it.
UNITS = {"fresh": 1.0, "write_5m": 1.25, "write_1h": 2.0, "read": 0.1, "out": 5.0}
LONG_PROMPT = 100_000
LONG_MODELS = ("haiku-5-5",)


def _zero_main() -> dict:
    return {"in": 0, "out": 0, "cacheRead": 0, "cacheWrite": 0, "units": 0.0}


def call_units(model: str, u: dict) -> float:
    """One call's cost in UNITS, from its assistant message's usage."""
    fresh, read, write, out = (_n(u, "input_tokens"), _n(u, "cache_read_input_tokens"),
                               _n(u, "cache_creation_input_tokens"), _n(u, "output_tokens"))
    split = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    w1h = min(split.get("ephemeral_1h_input_tokens") or 0, write)
    units = (fresh * UNITS["fresh"] + (write - w1h) * UNITS["write_5m"] + w1h * UNITS["write_1h"]
             + read * UNITS["read"] + out * UNITS["out"])
    if fresh + read + write > LONG_PROMPT and any(m in model for m in LONG_MODELS):
        units *= 5
    return units


def split_shared(rows: list[dict], main: dict[str, dict]) -> list[dict]:
    """When the orchestrator runs on the implementers' model, modelUsage holds both in one entry,
    which component_of gives to the implementer. The main thread's own calls since the last
    result (`main`, from the stream) become an orchestrator row and the rest stays the
    implementers'. The row's cost is split by units: the main thread's calls exactly, the rest
    from its token counts at the short-prompt prices."""
    out = []
    for row in rows:
        m = main.get(row["model"])
        if row["component"] != "implementer" or not m or not (m["in"] or m["out"]):
            out.append(row)
            continue
        cached = "cacheRead" in row
        orch = {"component": "orchestrator", "model": row["model"], "in": min(m["in"], row["in"]),
                "out": min(m["out"], row["out"])}
        rest = {"component": "implementer", "model": row["model"], "in": row["in"] - orch["in"],
                "out": row["out"] - orch["out"]}
        if cached:
            orch["cacheRead"] = min(m["cacheRead"], row["cacheRead"])
            orch["cacheWrite"] = min(m["cacheWrite"], row["cacheWrite"])
            rest["cacheRead"] = row["cacheRead"] - orch["cacheRead"]
            rest["cacheWrite"] = row["cacheWrite"] - orch["cacheWrite"]
        fresh = rest["in"] - rest.get("cacheRead", 0) - rest.get("cacheWrite", 0)
        rest_units = (max(0, fresh) * UNITS["fresh"] + rest.get("cacheWrite", 0) * UNITS["write_5m"]
                      + rest.get("cacheRead", 0) * UNITS["read"] + rest["out"] * UNITS["out"])
        total = m["units"] + rest_units
        orch["cost"] = row["cost"] * m["units"] / total if total > 0 else row["cost"]
        rest["cost"] = max(0, row["cost"] - orch["cost"])
        for r in (orch, rest):
            if row.get("ref"):
                r["ref"] = row["ref"]
            if r["in"] or r["out"]:
                out.append(r)
    return out


# `vfg usage add` takes at most 64 characters of --ref: the newest ids that fit.
MAX_REF = 64


def join_refs(refs: list[str]) -> str:
    kept: list[str] = []
    length = 0
    for r in reversed(refs):
        add = len(r) + (1 if kept else 0)
        if length + add > MAX_REF:
            break
        kept.insert(0, r)
        length += add
    return ",".join(kept)


def usage_args(row: dict) -> list[str]:
    args = ["usage", "add", "--component", row["component"], "--model", row["model"],
            "--in", js_num(row["in"]), "--out", js_num(row["out"]), "--cost", "%.6f" % row["cost"]]
    if "cacheRead" in row:
        args += ["--cache-read", js_num(row["cacheRead"]), "--cache-write", js_num(row["cacheWrite"])]
    if row.get("ref"):
        args += ["--ref", row["ref"]]
    return args


def _by_ref(a: str, b: str) -> int:
    d = js_number(a[1:]) - js_number(b[1:])
    if not math.isnan(d) and d != 0:
        return -1 if d < 0 else 1
    return (a > b) - (a < b)


class UsageMeter:
    """Tracks the task ids open at any time since the previous row, and writes the rows."""

    def __init__(self, store: Store, run: VfgRunner, implementer_model: str = "sonnet"):
        self.store = store
        self.run = run
        self.implementer_model = implementer_model
        self._refs: set[str] = set()
        # The main thread's calls since the last result, per model, and the message ids counted
        # (the stream repeats a message's usage on each of its content blocks).
        self._main: dict[str, dict] = {}
        self._seen: collections.deque[str] = collections.deque(maxlen=500)

    def note_main_call(self, message: dict) -> None:
        """An assistant message of the main thread: the orchestrator's own call."""
        mid, model, u = message.get("id"), message.get("model"), message.get("usage")
        if not model or not isinstance(u, dict) or (mid and mid in self._seen):
            return
        if mid:
            self._seen.append(mid)
        m = self._main.setdefault(model, _zero_main())
        read, write = _n(u, "cache_read_input_tokens"), _n(u, "cache_creation_input_tokens")
        m["in"] += _n(u, "input_tokens") + read + write
        m["out"] += _n(u, "output_tokens")
        m["cacheRead"] += read
        m["cacheWrite"] += write
        m["units"] += call_units(model, u)

    def note(self, ids: Iterable[str]) -> None:
        """A task was open at this moment."""
        self._refs.update(ids)

    def sorted_refs(self) -> list[str]:
        return sorted(self._refs, key=functools.cmp_to_key(_by_ref))

    async def on_result(self, model_usage: dict[str, dict] | None) -> list[dict]:
        """After a result: write a row per model whose totals moved."""
        self.note(self.store.open_ids())
        rows, snapshot = usage_deltas(self.store.state.usage, model_usage, self.sorted_refs(), self.implementer_model)
        rows = split_shared(rows, self._main)
        self._main = {}
        self.store.state.usage = snapshot
        self.store.save()
        if rows:
            self._refs = set(self.store.open_ids())
        for row in rows:
            r = await self.run(usage_args(row), 30_000)
            if r.code != 0:
                log("usage", "vfg usage add failed (%s): %s %s in=%s out=%s" % (why(r), row["component"], row["model"],
                                                                         js_num(row["in"]), js_num(row["out"])))
            else:
                cache = (" cache_read=%s cache_write=%s" % (js_num(row["cacheRead"]), js_num(row["cacheWrite"]))
                         if "cacheRead" in row else "")
                log("usage", "%s %s in=%s out=%s%s cost=%.4f%s" % (row["component"], row["model"], js_num(row["in"]), js_num(row["out"]),
                                                                cache, row["cost"], " ref=" + row["ref"] if row.get("ref") else ""))
        return rows
