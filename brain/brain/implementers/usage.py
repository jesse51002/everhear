"""One implementer run's usage, from its result's modelUsage, into `vfg usage add` rows.

A result's modelUsage is its session's running totals: a follow-up resumes the session, and the
CLI restores the totals it had, so a follow-up's row is its totals less the last run's (the
snapshot the runner keeps with the task). A total lower than the snapshot is a session that
started again from nothing, and counts from zero."""
from .config import js_num

Totals = dict[str, float]


def _n(u: dict, key: str):
    v = u.get(key)
    return 0 if v is None else v


def totals_of(u: dict) -> Totals:
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


ZERO: Totals = {"in": 0, "out": 0, "cost": 0, "cacheRead": 0, "cacheWrite": 0}


def rows(snapshot: dict[str, Totals], model_usage: dict[str, dict] | None,
         ref: str) -> tuple[list[dict], dict[str, Totals]]:
    """The rows for one result, one per model whose totals moved: its totals less the snapshot's.
    Returns the rows and the new snapshot."""
    nxt = dict(snapshot)
    out = []
    for model, u in (model_usage or {}).items():
        if not isinstance(u, dict):
            continue
        now = totals_of(u)
        prev = snapshot.get(model) or ZERO
        fell = (any(now[k] < prev[k] for k in ("in", "out", "cacheRead", "cacheWrite"))
                or now["cost"] < prev["cost"] - 1e-9)
        base = ZERO if fell else prev
        nxt[model] = now
        row = {"component": "implementer", "model": model, "ref": ref,
               **{k: now[k] - base[k] for k in ("in", "out", "cacheRead", "cacheWrite")},
               "cost": max(0, now["cost"] - base["cost"])}
        if row["in"] or row["out"]:
            out.append(row)
    return out, nxt


def usage_args(row: dict) -> list[str]:
    """`vfg usage add`'s arguments for one row."""
    return ["usage", "add", "--component", row["component"], "--model", row["model"],
            "--in", js_num(row["in"]), "--out", js_num(row["out"]), "--cost", "%.6f" % row["cost"],
            "--cache-read", js_num(row["cacheRead"]), "--cache-write", js_num(row["cacheWrite"]),
            "--ref", row["ref"]]
