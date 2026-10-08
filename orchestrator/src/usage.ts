// Per-model usage deltas from each result's modelUsage (the session's running totals,
// subagents included) into `vfg usage add` rows.
import type { ModelUsage } from "@anthropic-ai/claude-agent-sdk";
import { log } from "./config";
import type { ModelTotals, Store } from "./tasks";
import { type VfgRunner, why } from "./vfg";

export interface UsageRow {
  component: "orchestrator" | "implementer";
  model: string;
  in: number;
  out: number;
  cost: number;
  ref?: string;
}

/**
 * Which component a model's usage belongs to: the implementer's model (an alias such as "sonnet",
 * or a full id) is the implementer; any other model is the orchestrator. Each row carries that
 * model's own costUSD from the SDK, so it is priced as that model.
 */
export function componentOf(model: string, implementerModel = "sonnet"): "orchestrator" | "implementer" {
  const m = model.toLowerCase();
  const impl = implementerModel.toLowerCase();
  return m === impl || m.includes(impl) ? "implementer" : "orchestrator";
}

export function totalsOf(u: ModelUsage): ModelTotals {
  return {
    in: (u.inputTokens ?? 0) + (u.cacheReadInputTokens ?? 0) + (u.cacheCreationInputTokens ?? 0),
    out: u.outputTokens ?? 0,
    cost: u.costUSD ?? 0,
  };
}

/**
 * The rows for one result: for each model, its totals minus the snapshot; a total lower than
 * the snapshot (a new session) counts from zero. Returns the rows and the new snapshot.
 */
export function usageDeltas(
  snapshot: Record<string, ModelTotals>,
  modelUsage: Record<string, ModelUsage> | undefined,
  refs: string[],
  implementerModel = "sonnet",
): { rows: UsageRow[]; snapshot: Record<string, ModelTotals> } {
  const next: Record<string, ModelTotals> = { ...snapshot };
  const rows: UsageRow[] = [];
  for (const [model, u] of Object.entries(modelUsage ?? {})) {
    const now = totalsOf(u);
    const prev = snapshot[model] ?? { in: 0, out: 0, cost: 0 };
    const reset = now.in < prev.in || now.out < prev.out || now.cost < prev.cost - 1e-9;
    const base = reset ? { in: 0, out: 0, cost: 0 } : prev;
    const d = { in: now.in - base.in, out: now.out - base.out, cost: Math.max(0, now.cost - base.cost) };
    next[model] = now;
    if (d.in === 0 && d.out === 0) continue;
    const row: UsageRow = { component: componentOf(model, implementerModel), model, in: d.in, out: d.out, cost: d.cost };
    const ref = joinRefs(refs);
    if (ref) row.ref = ref;
    rows.push(row);
  }
  return { rows, snapshot: next };
}

/** `vfg usage add` takes at most 64 characters of --ref: the newest ids that fit. */
export const MAX_REF = 64;
export function joinRefs(refs: string[]): string {
  const kept: string[] = [];
  let len = 0;
  for (let i = refs.length - 1; i >= 0; i--) {
    const add = refs[i]!.length + (kept.length ? 1 : 0);
    if (len + add > MAX_REF) break;
    kept.unshift(refs[i]!);
    len += add;
  }
  return kept.join(",");
}

export function usageArgs(row: UsageRow): string[] {
  const args = ["usage", "add", "--component", row.component, "--model", row.model, "--in", String(row.in), "--out", String(row.out), "--cost", row.cost.toFixed(6)];
  if (row.ref) args.push("--ref", row.ref);
  return args;
}

/** Tracks the task ids open at any time since the previous row, and writes the rows. */
export class UsageMeter {
  private refs = new Set<string>();

  constructor(
    private readonly store: Store,
    private readonly run: VfgRunner,
    /** In direct mode every model call is the orchestrator's. */
    private readonly mode: "direct" | "implementers" | "hybrid" = "implementers",
    private readonly implementerModel = "sonnet",
  ) {}

  /** A task was open at this moment. */
  note(ids: Iterable<string>): void {
    for (const id of ids) this.refs.add(id);
  }

  sortedRefs(): string[] {
    return [...this.refs].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)) || a.localeCompare(b));
  }

  /** After a result: write a row per model whose totals moved. */
  async onResult(modelUsage: Record<string, ModelUsage> | undefined): Promise<UsageRow[]> {
    this.note(this.store.openIds());
    const { rows, snapshot } = usageDeltas(this.store.state.usage, modelUsage, this.sortedRefs(), this.implementerModel);
    if (this.mode === "direct") for (const r of rows) r.component = "orchestrator";
    this.store.state.usage = snapshot;
    this.store.save();
    if (rows.length) this.refs = new Set(this.store.openIds());
    for (const row of rows) {
      const r = await this.run(usageArgs(row), 30_000);
      if (r.code !== 0) log("usage", `vfg usage add failed (${why(r)}): ${row.component} ${row.model} in=${row.in} out=${row.out}`);
      else log("usage", `${row.component} ${row.model} in=${row.in} out=${row.out} cost=${row.cost.toFixed(4)}${row.ref ? " ref=" + row.ref : ""}`);
    }
    return rows;
  }
}
