"""Per-model usage deltas from each result's modelUsage (the session's running totals,
subagents included) into `vfg usage add` rows."""
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
    """One model's totals from the SDK's modelUsage entry (inputTokens, cacheReadInputTokens, …)."""
    return {
        "in": _n(u, "inputTokens") + _n(u, "cacheReadInputTokens") + _n(u, "cacheCreationInputTokens"),
        "out": _n(u, "outputTokens"),
        "cost": _n(u, "costUSD"),
    }


def usage_deltas(snapshot: dict[str, ModelTotals], model_usage: dict[str, dict] | None, refs: list[str],
                 implementer_model: str = "sonnet") -> tuple[list[dict], dict[str, ModelTotals]]:
    """The rows for one result: for each model, its totals minus the snapshot; a total lower than
    the snapshot (a new session) counts from zero. Returns the rows and the new snapshot."""
    nxt = dict(snapshot)
    rows = []
    for model, u in (model_usage or {}).items():
        now = totals_of(u)
        prev = snapshot.get(model) or {"in": 0, "out": 0, "cost": 0}
        reset = now["in"] < prev["in"] or now["out"] < prev["out"] or now["cost"] < prev["cost"] - 1e-9
        base = {"in": 0, "out": 0, "cost": 0} if reset else prev
        d = {"in": now["in"] - base["in"], "out": now["out"] - base["out"], "cost": max(0, now["cost"] - base["cost"])}
        nxt[model] = now
        if d["in"] == 0 and d["out"] == 0:
            continue
        row = {"component": component_of(model, implementer_model), "model": model,
               "in": d["in"], "out": d["out"], "cost": d["cost"]}
        ref = join_refs(refs)
        if ref:
            row["ref"] = ref
        rows.append(row)
    return rows, nxt


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

    def note(self, ids: Iterable[str]) -> None:
        """A task was open at this moment."""
        self._refs.update(ids)

    def sorted_refs(self) -> list[str]:
        return sorted(self._refs, key=functools.cmp_to_key(_by_ref))

    async def on_result(self, model_usage: dict[str, dict] | None) -> list[dict]:
        """After a result: write a row per model whose totals moved."""
        self.note(self.store.open_ids())
        rows, snapshot = usage_deltas(self.store.state.usage, model_usage, self.sorted_refs(), self.implementer_model)
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
                log("usage", "%s %s in=%s out=%s cost=%.4f%s" % (row["component"], row["model"], js_num(row["in"]), js_num(row["out"]),
                                                              row["cost"], " ref=" + row["ref"] if row.get("ref") else ""))
        return rows
