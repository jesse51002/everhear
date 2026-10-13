"""One implementer run's usage, priced by us: the harness reports each model request's tokens (a
Request event), each is priced from the table in config.py at its own size and time, and the run
sums them into `vfg usage add` rows, one per model, so one per run (every model Claude Code may call
is set to the same one). When the provider says what each request was billed (OpenRouter's
`usage.cost`, Request.billed), and every request of the run has it, the row's cost is that; else the
table's. The table's price is kept beside it (list_cost) either way, so an eval compares at list
price.

Reasoning tokens are a part of a request's output, priced once, as output; each row says how many
of its output tokens they were (`reasoning`, for the log: `vfg usage add` has no column for it), and
how many of its requests broke off mid-stream (`broken`: each counted from the provider's record of
it, else as far as it was read, a floor).

No harness's running totals are read. Claude Code's modelUsage is its session's running total, and
a session resumed after the brain restarted logged its whole history again as one run's; a run
here is the requests it made, and nothing else."""
from .config import js_num, log, price_at
from .harness.base import Request

# The models the table has no price for, each logged once.
_unpriced: set[str] = set()


def cost(r: Request) -> float:
    """One request's dollars, at its size and time; 0 for a model the table does not have."""
    p = price_at(r.model, r.size, r.at)
    if p is None:
        if r.model not in _unpriced:
            _unpriced.add(r.model)
            log("usage", "no price for %s: its requests are counted at $0" % (r.model or "an unnamed model"))
        return 0.0
    return (r.input * p.input + r.cache_read * p.hit + r.cache_write * p.write + r.output * p.output) / 1e6


class RunUsage:
    """One run's requests, summed by model."""

    def __init__(self, ref: str):
        self.ref = ref
        self._models: dict[str, dict] = {}

    def add(self, r: Request) -> float:
        """Counts one request; returns its cost."""
        c = cost(r)
        m = self._models.setdefault(r.model, {"in": 0, "out": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0,
                                              "cost": 0.0, "largest": 0, "requests": 0, "broken": 0, "billed": 0.0,
                                              "billed_all": True})
        m["in"] += r.size
        m["out"] += r.output
        m["reasoning"] += r.reasoning
        m["cacheRead"] += r.cache_read
        m["cacheWrite"] += r.cache_write
        m["cost"] += c
        m["largest"] = max(m["largest"], r.size)
        m["requests"] += 1
        m["broken"] += 1 if r.broken else 0
        if r.billed is None:
            m["billed_all"] = False
        else:
            m["billed"] += r.billed
        return c

    def rows(self) -> list[dict]:
        """The run's rows: "in" counts every input token, cacheRead and cacheWrite the parts of it read
        from and written to the cache; "reasoning" the part of "out" that was thinking; largest is its
        largest request, in tokens; "broken" how many of its requests broke off mid-stream."""
        out = []
        for model, m in self._models.items():
            if not (m["in"] or m["out"]):
                continue
            billed = m["billed_all"] and m["requests"] > 0
            out.append({"component": "implementer", "model": model, "ref": self.ref,
                        **{k: m[k] for k in ("in", "out", "reasoning", "cacheRead", "cacheWrite", "largest", "broken")},
                        "cost": m["billed"] if billed else m["cost"], "list_cost": m["cost"],
                        "priced_by": "the provider" if billed else "our table"})
        return out


def usage_args(row: dict) -> list[str]:
    """`vfg usage add`'s arguments for one row."""
    return ["usage", "add", "--component", row["component"], "--model", row["model"],
            "--in", js_num(row["in"]), "--out", js_num(row["out"]), "--cost", "%.6f" % row["cost"],
            "--cache-read", js_num(row["cacheRead"]), "--cache-write", js_num(row["cacheWrite"]),
            "--largest", js_num(row["largest"]), "--ref", row["ref"]]
