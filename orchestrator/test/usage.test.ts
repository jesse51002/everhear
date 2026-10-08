import { describe, expect, test } from "bun:test";
import { componentOf, joinRefs, usageArgs, usageDeltas, UsageMeter } from "../src/usage";
import { vfg } from "../src/vfg";
import { newStore, tempDir, useFakeVfg } from "./helpers";

const mu = (input: number, output: number, cost: number, cacheRead = 0, cacheWrite = 0) =>
  ({ inputTokens: input, outputTokens: output, cacheReadInputTokens: cacheRead, cacheCreationInputTokens: cacheWrite, webSearchRequests: 0, costUSD: cost, contextWindow: 200000, maxOutputTokens: 32000 }) as any;

const OPUS = "claude-opus-4-8";
const HAIKU = "claude-haiku-4-6";
const SONNET = "claude-sonnet-4-6";

describe("usage deltas", () => {
  test("a first result: one row per model, in = input + cache read + cache write", () => {
    const { rows, snapshot } = usageDeltas({}, { [OPUS]: mu(100, 50, 0.5, 1000, 200), [SONNET]: mu(10, 5, 0.03) }, ["T1"]);
    expect(rows).toEqual([
      { component: "orchestrator", model: OPUS, in: 1300, out: 50, cost: 0.5, ref: "T1" },
      { component: "implementer", model: SONNET, in: 10, out: 5, cost: 0.03, ref: "T1" },
    ]);
    expect(snapshot[OPUS]).toEqual({ in: 1300, out: 50, cost: 0.5 });
  });

  test("a resumed session: the delta from the snapshot", () => {
    const snap = { [OPUS]: { in: 1300, out: 50, cost: 0.5 } };
    const { rows } = usageDeltas(snap, { [OPUS]: mu(200, 80, 0.75, 1000, 200) }, []);
    expect(rows).toEqual([{ component: "orchestrator", model: OPUS, in: 100, out: 30, cost: 0.25 }]);
  });

  test("a lower total (a new session) counts from zero", () => {
    const snap = { [OPUS]: { in: 5000, out: 900, cost: 2 } };
    const { rows, snapshot } = usageDeltas(snap, { [OPUS]: mu(40, 20, 0.1) }, []);
    expect(rows).toEqual([{ component: "orchestrator", model: OPUS, in: 40, out: 20, cost: 0.1 }]);
    expect(snapshot[OPUS]).toEqual({ in: 40, out: 20, cost: 0.1 });
  });

  test("an unchanged model writes no row; refs are left out when none", () => {
    const snap = { [HAIKU]: { in: 10, out: 5, cost: 0.01 } };
    const { rows } = usageDeltas(snap, { [HAIKU]: mu(10, 5, 0.01) }, []);
    expect(rows).toEqual([]);
    expect(usageArgs({ component: "implementer", model: HAIKU, in: 1, out: 2, cost: 0.5 })).toEqual(
      ["usage", "add", "--component", "implementer", "--model", HAIKU, "--in", "1", "--out", "2", "--cost", "0.500000"],
    );
  });

  test("refs fit in vfg's 64 characters, newest kept", () => {
    const many = Array.from({ length: 30 }, (_, i) => `T${i + 1}`);
    const ref = joinRefs(many);
    expect(ref.length).toBeLessThanOrEqual(64);
    expect(ref.endsWith("T29,T30")).toBe(true);
    expect(ref.split(",").every((x) => /^T\d+$/.test(x))).toBe(true);
    expect(joinRefs(["T1", "T2"])).toBe("T1,T2");
    expect(joinRefs([])).toBe("");
  });

  test("the implementer's model is the implementer, anything else the orchestrator", () => {
    expect(componentOf("claude-sonnet-4-6")).toBe("implementer"); // default implementer: sonnet
    expect(componentOf("claude-opus-4-8")).toBe("orchestrator");
    expect(componentOf("claude-haiku-4-6")).toBe("orchestrator");
    expect(componentOf("claude-haiku-4-6", "haiku")).toBe("implementer");
    expect(componentOf("claude-3-5-HAIKU", "haiku")).toBe("implementer");
    expect(componentOf("claude-sonnet-5", "haiku")).toBe("orchestrator");
    expect(componentOf("claude-sonnet-4-6", "claude-sonnet-4-6")).toBe("implementer");
  });

  test("each row carries its own model's cost: Sonnet is priced as Sonnet", () => {
    const { rows } = usageDeltas({}, { [OPUS]: mu(1000, 100, 0.09), [SONNET]: mu(1000, 100, 0.0045) }, [], "sonnet");
    expect(rows.find((r) => r.model === SONNET)).toEqual({ component: "implementer", model: SONNET, in: 1000, out: 100, cost: 0.0045 });
    expect(rows.find((r) => r.model === OPUS)!.cost).toBe(0.09);
  });
});

describe("usage meter", () => {
  test("direct mode: every row is the orchestrator's", async () => {
    const dir = tempDir();
    const fake = useFakeVfg(dir);
    const store = newStore(dir);
    await new UsageMeter(store, vfg, "direct").onResult({ [HAIKU]: mu(50, 5, 0.01) });
    expect(fake.calls().filter((c) => c[0] === "usage").map((c) => c[3])).toEqual(["orchestrator"]);
  });

  test("rows go to vfg usage add with the tasks open since the previous row", async () => {
    const dir = tempDir();
    const fake = useFakeVfg(dir);
    const store = newStore(dir);
    const meter = new UsageMeter(store, vfg);
    store.add("T1", { at: "x", task: "a", context: "" });
    store.add("T2", { at: "x", task: "b", context: "" });
    meter.note(["T1", "T2"]);
    store.reply("T1", "done", "ok");
    await meter.onResult({ [OPUS]: mu(100, 10, 0.2), [SONNET]: mu(50, 5, 0.01) });
    // T2 is still open; T3 opens and closes before the next row
    store.add("T3", { at: "x", task: "c", context: "" });
    meter.note(["T3"]);
    store.reply("T3", "done", "ok");
    store.reply("T2", "done", "ok");
    await meter.onResult({ [OPUS]: mu(150, 20, 0.3), [SONNET]: mu(50, 5, 0.01) });
    // nothing open since: no ref
    await meter.onResult({ [OPUS]: mu(160, 25, 0.31), [SONNET]: mu(50, 5, 0.01) });
    const rows = fake.calls().filter((c) => c[0] === "usage");
    expect(rows).toEqual([
      ["usage", "add", "--component", "orchestrator", "--model", OPUS, "--in", "100", "--out", "10", "--cost", "0.200000", "--ref", "T1,T2"],
      ["usage", "add", "--component", "implementer", "--model", SONNET, "--in", "50", "--out", "5", "--cost", "0.010000", "--ref", "T1,T2"],
      ["usage", "add", "--component", "orchestrator", "--model", OPUS, "--in", "50", "--out", "10", "--cost", "0.100000", "--ref", "T2,T3"],
      ["usage", "add", "--component", "orchestrator", "--model", OPUS, "--in", "10", "--out", "5", "--cost", "0.010000"],
    ]);
    expect(newStore(dir).state.usage[OPUS]).toEqual({ in: 160, out: 25, cost: 0.31 });
  });
});
