import { describe, expect, test } from "bun:test";
import type { Options, SDKMessage, SDKUserMessage } from "@anthropic-ai/claude-agent-sdk";
import { Outbox } from "../src/reply";
import { buildOptions, FatalError, MSG, rules, Session, sessionEnv } from "../src/session";
import { UsageMeter } from "../src/usage";
import { vfg } from "../src/vfg";
import { newStore, tempDir, testConfig, until, useFakeVfg } from "./helpers";

/** A stand-in for the SDK's Query: it records the prompts it pulls, and emits what a test tells it to. */
class FakeQuery {
  received: string[] = [];
  stopTasks: string[] = [];
  private queue: SDKMessage[] = [];
  private waiter: ((r: IteratorResult<SDKMessage>) => void) | null = null;
  ended = false;
  constructor(
    prompt: AsyncIterable<SDKUserMessage>,
    readonly options: Options,
    private readonly info: Record<string, string> = { apiKeySource: "none", subscriptionType: "max" },
  ) {
    void (async () => {
      for await (const m of prompt) this.received.push(m.message.content as string);
    })();
  }
  emit(m: { type: string; [k: string]: unknown }): void {
    const msg = { session_id: "S1", ...m } as unknown as SDKMessage;
    if (this.waiter) {
      const w = this.waiter;
      this.waiter = null;
      w({ done: false, value: msg });
    } else this.queue.push(msg);
  }
  end(): void {
    this.ended = true;
    const w = this.waiter;
    this.waiter = null;
    w?.({ done: true, value: undefined });
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  next(): Promise<IteratorResult<SDKMessage>> {
    const m = this.queue.shift();
    if (m) return Promise.resolve({ done: false, value: m });
    if (this.ended) return Promise.resolve({ done: true, value: undefined });
    return new Promise((res) => (this.waiter = res));
  }
  async accountInfo() {
    return this.info;
  }
  async stopTask(id: string) {
    this.stopTasks.push(id);
  }
  interrupts = 0;
  async interrupt() {
    this.interrupts++;
    return undefined;
  }
  close() {
    this.end();
  }
}

const result = (over: object = {}) => ({
  type: "result", subtype: "success", is_error: false, num_turns: 1, duration_ms: 1000, result: "ok",
  modelUsage: {}, permission_denials: [], queued_turn_count: 0, ...over,
});
const started = (task_id: string, description: string, tool_use_id = "") => ({
  type: "system", subtype: "task_started", task_id, tool_use_id, description, task_type: "local_agent",
});
const notified = (task_id: string, status = "completed") => ({ type: "system", subtype: "task_notification", task_id, status, summary: "done", output_file: "" });
const tick = () => new Promise((r) => setTimeout(r, 5));

function setup(info?: Record<string, string>, mode: "implementers" | "direct" | "hybrid" = "implementers", extra: Record<string, string> = {}) {
  const dir = tempDir();
  const fake = useFakeVfg(dir);
  const cfg = testConfig(dir, { VFG_ORCH_MODE: mode, ...extra });
  const store = newStore(dir);
  const outbox = new Outbox(store, vfg, [5], 5);
  const usage = new UsageMeter(store, vfg, mode);
  const queries: FakeQuery[] = [];
  let clock = 1_000_000;
  const session = new Session({
    cfg, store, outbox, usage,
    prompts: { orchestrator: "orch %REPO% %MAX_IMPLEMENTERS%", implementer: "impl" },
    queryFn: ({ prompt, options }) => {
      const q = new FakeQuery(prompt, options, info);
      queries.push(q);
      return q as any;
    },
    now: () => clock,
    delays: { first: 10, max: 40, healthy: 600_000 },
  });
  const open = (id: string) => store.add(id, { at: new Date().toISOString(), task: "do " + id, context: "" });
  return { dir, fake, cfg, store, session, queries, open, advance: (ms: number) => (clock += ms), q: () => queries.at(-1)! };
}

describe("session, implementers mode (the default)", () => {
  test("the session id of the first message is saved", async () => {
    const s = setup();
    await s.session.open();
    s.q().emit({ type: "system", subtype: "init", session_id: "abc", model: "opus", permissionMode: "dontAsk", tools: [] } as any);
    await until(() => s.store.state.sessionId === "abc");
    expect(s.dir && s.store.state.sessionId).toBe("abc");
  });

  test("idle with a task open: nudged once, closed at the next idle moment", async () => {
    const s = setup();
    s.open("T1");
    await s.session.open();
    s.q().emit(result());
    await until(() => s.q().received.length === 1);
    expect(s.q().received[0]).toBe(MSG.nudge(["T1"]));
    expect(s.store.get("T1")!.nudged).toBe(true);
    s.session.tick(); // the nudge was taken: a turn is coming, so not idle
    expect(s.q().received.length).toBe(1);
    s.q().emit(result());
    await until(() => s.q().received.length === 2);
    expect(s.q().received[1]).toBe(MSG.closed(["T1"]));
    const t = s.store.get("T1")!;
    expect(t.status).toBe("failed");
    expect(t.replies.map((r) => r.text)).toEqual([MSG.noAnswer]);
  });

  test("an error result: the task is closed with the error", async () => {
    const s = setup();
    s.open("T2");
    await s.session.open();
    const err = { subtype: "error_during_execution", is_error: true, errors: ["API Error: 429 rate_limit_error " + "x".repeat(200)] };
    s.q().emit(result(err));
    await until(() => s.q().received.length === 1);
    s.q().emit(result(err));
    await until(() => s.store.get("T2")!.status === "failed");
    const text = s.store.get("T2")!.replies[0]!.text;
    expect(text.startsWith("The orchestrator could not run: API Error: 429 rate_limit_error")).toBe(true);
    expect(text.length).toBe("The orchestrator could not run: ".length + 120);
  });

  test("a replied task is not nudged", async () => {
    const s = setup();
    s.open("T1");
    await s.session.open();
    s.store.reply("T1", "done", "day");
    s.q().emit(result());
    await tick();
    expect(s.q().received).toEqual([]);
  });

  test("a running implementer keeps it from idling; its start clears nudged", async () => {
    const s = setup();
    s.open("T1");
    s.store.get("T1")!.nudged = true;
    await s.session.open();
    s.session.guardContext().onAgentAllowed("T1", "u1");
    expect(s.session.guardContext().activeImplementers()).toBe(1);
    s.q().emit(started("k1", "T1 make it day", "u1"));
    await until(() => s.session.running.size === 1);
    expect(s.session.pending.size).toBe(0);
    expect(s.store.get("T1")!.nudged).toBe(false);
    s.q().emit(result());
    await tick();
    expect(s.q().received).toEqual([]); // running: not idle
    s.q().emit(notified("k1"));
    await until(() => s.session.running.size === 0);
    s.session.tick();
    expect(s.q().received).toEqual([]); // its report wakes a turn: wait for it
    s.advance(61_000);
    s.session.tick();
    await until(() => s.q().received.length === 1);
    expect(s.q().received[0]).toBe(MSG.nudge(["T1"]));
  });

  test("background_tasks_changed replaces the running set", async () => {
    const s = setup();
    s.open("T1");
    await s.session.open();
    s.session.guardContext().onAgentAllowed("T1", "u1");
    s.q().emit({ type: "system", subtype: "background_tasks_changed", tasks: [
      { task_id: "k1", task_type: "local_agent", subagent_type: "implementer", description: "T1 day" },
      { task_id: "b1", task_type: "local_bash", description: "sleep 5" },
    ] });
    await until(() => s.session.running.size === 1);
    expect(s.session.pending.size).toBe(0);
    s.q().emit(result());
    await tick();
    expect(s.q().received).toEqual([]);
    s.q().emit({ type: "system", subtype: "background_tasks_changed", tasks: [] });
    await until(() => s.session.running.size === 0);
  });

  test("allowed implementers that never start stop counting after a minute", async () => {
    const s = setup();
    await s.session.open();
    s.session.guardContext().onAgentAllowed("T1", "u9");
    s.advance(61_000);
    s.session.tick();
    expect(s.session.pending.size).toBe(0);
  });

  test("an implementer over its time limit is stopped", async () => {
    const s = setup();
    s.open("T1");
    await s.session.open();
    s.q().emit(started("k1", "T1 castle"));
    await until(() => s.session.running.size === 1);
    s.advance(9 * 60_000);
    s.session.tick();
    expect(s.q().stopTasks).toEqual([]);
    s.advance(2 * 60_000);
    s.session.tick();
    s.session.tick();
    await tick();
    expect(s.q().stopTasks).toEqual(["k1"]);
  });

  test("an API key is fatal", async () => {
    const s = setup({ apiKeySource: "ANTHROPIC_API_KEY" });
    await expect(s.session.open()).rejects.toBeInstanceOf(FatalError);
  });

  test("usage rows land after a result", async () => {
    const s = setup();
    s.open("T1");
    await s.session.open();
    s.q().emit(result({ modelUsage: { "claude-opus-4-8": { inputTokens: 10, outputTokens: 2, cacheReadInputTokens: 5, cacheCreationInputTokens: 0, costUSD: 0.01 }, "claude-sonnet-4-6": { inputTokens: 3, outputTokens: 1, cacheReadInputTokens: 0, cacheCreationInputTokens: 0, costUSD: 0.001 } } }));
    await until(() => s.fake.calls().filter((c) => c[0] === "usage").length === 2);
    await s.session.settled();
    const rows = s.fake.calls().filter((c) => c[0] === "usage").map((c) => c.slice(2, 4).join(" "));
    expect(rows.sort()).toEqual(["--component implementer", "--component orchestrator"]);
    expect(s.fake.calls().find((c) => c[3] === "orchestrator")).toContain("T1");
  });

  test("a session that ends: open tasks failed as interrupted, resumed, told", async () => {
    const s = setup();
    s.store.state.sessionId = "S1";
    await s.session.open("S1");
    const keeper = s.session.keepAlive(0);
    s.q().emit(result());
    await tick();
    s.open("T3");
    s.advance(60_000);
    s.q().end();
    await until(() => s.queries.length === 2);
    expect(s.store.get("T3")!.status).toBe("failed");
    expect(s.store.get("T3")!.replies[0]!.text).toBe(MSG.interrupted);
    expect(s.queries[1]!.options.resume).toBe("S1");
    await until(() => s.queries[1]!.received.length === 1);
    expect(s.queries[1]!.received[0]).toMatch(/^RUNNER: restarted at \d\d:\d\d:\d\d UTC\. Closed as failed: T3\. Implementers from before are gone/);
    s.session.stopping = true;
    s.session.closeQuery();
    await keeper;
  });

  test("a resume that fails before any turn: a new session", async () => {
    const s = setup();
    s.store.state.sessionId = "gone";
    s.store.state.usage = { m: { in: 1, out: 1, cost: 1 } };
    await s.session.open("gone");
    const keeper = s.session.keepAlive(1_000_000);
    s.q().end(); // the CLI exits: no conversation with that id
    await until(() => s.queries.length === 2);
    expect(s.queries[1]!.options.resume).toBeUndefined();
    expect(s.store.state.sessionId).toBeUndefined();
    expect(s.store.state.usage).toEqual({});
    s.session.stopping = true;
    s.session.closeQuery();
    await keeper;
  });

  test("restarts back off from 5 s, doubling", async () => {
    const s = setup();
    await s.session.open();
    const keeper = s.session.keepAlive(1_000_000);
    s.q().emit(result());
    await tick();
    for (let i = 0; i < 4; i++) {
      s.q().emit(result());
      await tick();
      s.q().end();
      await until(() => s.queries.length === i + 2, 2000);
    }
    s.session.stopping = true;
    s.session.closeQuery();
    await keeper;
    expect(s.queries.length).toBe(5);
  });

  test("stop fails every open task as stopped", async () => {
    const s = setup();
    s.open("T1");
    s.open("T2");
    await s.session.open();
    expect(await s.session.stop()).toEqual(["T1", "T2"]);
    expect(s.store.get("T2")!.replies[0]!.text).toBe(MSG.stopped);
    s.session.closeQuery();
  });
});

describe("session, direct mode (VFG_ORCH_MODE=direct)", () => {
  const task = (id: string) => `TASK ${id} at 00:00:00 UTC\ndo ${id}`;

  test("tasks go to the session one at a time", async () => {
    const s = setup(undefined, "direct");
    await s.session.open();
    s.open("T1");
    s.open("T2");
    s.session.pushTask("T1", task("T1"));
    s.session.pushTask("T2", task("T2"));
    await until(() => s.q().received.length === 1);
    await tick();
    expect(s.q().received).toEqual([task("T1")]);
    expect(s.session.waiting.map((w) => w.id)).toEqual(["T2"]);
    s.store.reply("T1", "done", "ok");
    s.q().emit(result());
    await until(() => s.q().received.length === 2);
    expect(s.q().received[1]).toBe(task("T2"));
    s.store.reply("T2", "done", "ok");
    s.q().emit(result());
    await tick();
    expect(s.q().received.length).toBe(2);
  });

  test("a waiting task is never nudged; delivered ones are, after the queue is through", async () => {
    const s = setup(undefined, "direct");
    await s.session.open();
    s.open("T1");
    s.open("T2");
    s.session.pushTask("T1", task("T1"));
    s.session.pushTask("T2", task("T2"));
    await until(() => s.q().received.length === 1);
    s.q().emit(result()); // T1's turn ended without a reply: T2 goes in, nobody is nudged yet
    await until(() => s.q().received.length === 2);
    expect(s.q().received[1]).toBe(task("T2"));
    expect(s.store.get("T1")!.nudged).toBe(false);
    s.q().emit(result());
    await until(() => s.q().received.length === 3);
    expect(s.q().received[2]).toBe(MSG.nudge(["T1", "T2"], "direct"));
    expect(MSG.nudge(["T1"], "direct")).toContain("do what it needs");
  });

  test("a task closed while waiting is skipped", async () => {
    const s = setup(undefined, "direct");
    await s.session.open();
    s.open("T1");
    s.open("T2");
    s.open("T3");
    s.session.pushTask("T1", task("T1"));
    s.session.pushTask("T2", task("T2"));
    s.session.pushTask("T3", task("T3"));
    await until(() => s.q().received.length === 1);
    s.store.reply("T2", "failed", "cancelled");
    s.store.reply("T1", "done", "ok");
    s.q().emit(result());
    await until(() => s.q().received.length === 2);
    expect(s.q().received[1]).toBe(task("T3"));
  });

  test("a session restart fails the task in hand and keeps the waiting ones", async () => {
    const s = setup(undefined, "direct");
    await s.session.open();
    const keeper = s.session.keepAlive(0);
    s.open("T1");
    s.open("T2");
    s.session.pushTask("T1", task("T1"));
    s.session.pushTask("T2", task("T2"));
    await until(() => s.q().received.length === 1);
    s.advance(60_000);
    s.q().end();
    await until(() => s.queries.length === 2);
    expect(s.store.get("T1")!.status).toBe("failed");
    expect(s.store.get("T1")!.replies[0]!.text).toBe(MSG.interrupted);
    expect(s.store.get("T2")!.status).toBe("open");
    await until(() => s.queries[1]!.received.length === 1);
    expect(s.queries[1]!.received[0]).toContain("Work in progress was cut off");
    s.queries[1]!.emit(result()); // the note's turn ends: T2 goes in
    await until(() => s.queries[1]!.received.length === 2);
    expect(s.queries[1]!.received[1]).toBe(task("T2"));
    s.session.stopping = true;
    s.session.closeQuery();
    await keeper;
  });

  test("a turn over the time limit is interrupted and told to reply failed", async () => {
    const s = setup(undefined, "direct");
    await s.session.open();
    s.open("T1");
    s.session.pushTask("T1", task("T1"));
    await until(() => s.q().received.length === 1);
    s.advance(9 * 60_000);
    s.session.tick();
    expect(s.q().interrupts).toBe(0);
    s.advance(2 * 60_000);
    s.session.tick();
    s.session.tick();
    await tick();
    expect(s.q().interrupts).toBe(1);
    await until(() => s.q().received.length === 2);
    expect(s.q().received[1]).toBe(MSG.overtime(10));
  });

  test("stop fails waiting tasks too", async () => {
    const s = setup(undefined, "direct");
    await s.session.open();
    s.open("T1");
    s.open("T2");
    s.session.pushTask("T1", task("T1"));
    s.session.pushTask("T2", task("T2"));
    expect(await s.session.stop()).toEqual(["T1", "T2"]);
    expect(s.session.waiting).toEqual([]);
    s.session.closeQuery();
  });

  test("usage rows are the orchestrator's, whatever the model", async () => {
    const s = setup(undefined, "direct");
    await s.session.open();
    s.q().emit(result({ modelUsage: { "claude-haiku-4-6": { inputTokens: 3, outputTokens: 1, cacheReadInputTokens: 0, cacheCreationInputTokens: 0, costUSD: 0.001 } } }));
    await until(() => s.fake.calls().some((c) => c[0] === "usage"));
    await s.session.settled();
    expect(s.fake.calls().find((c) => c[0] === "usage")).toContain("orchestrator");
  });
});

describe("session, hybrid mode (the default)", () => {
  const task = (id: string) => `TASK ${id} at 00:00:00 UTC\ndo ${id}`;

  test("a quick task goes in while an implementer works, and waits only for the session's own turn", async () => {
    const s = setup(undefined, "hybrid");
    await s.session.open();
    s.open("T3");
    s.session.pushTask("T3", task("T3"));
    await until(() => s.q().received.length === 1);
    s.q().emit(started("k1", "T3 build a house", "u1")); // the house goes to an implementer
    s.q().emit(result());
    await until(() => s.session.running.size === 1 && !s.session.busy);
    s.open("T4");
    s.session.pushTask("T4", task("T4")); // a quick one, while the house is built
    await until(() => s.q().received.length === 2);
    expect(s.q().received[1]).toBe(task("T4"));
    s.open("T5");
    s.session.pushTask("T5", task("T5")); // the session is busy with T4: T5 waits
    await tick();
    expect(s.q().received.length).toBe(2);
    s.store.reply("T4", "done", "rain");
    s.q().emit(result());
    await until(() => s.q().received.length === 3);
    expect(s.q().received[2]).toBe(task("T5"));
  });

  test("an implementer running keeps the session from being idle", async () => {
    const s = setup(undefined, "hybrid");
    await s.session.open();
    s.open("T3");
    s.session.pushTask("T3", task("T3"));
    await until(() => s.q().received.length === 1);
    s.q().emit(started("k1", "T3 build a house", "u1"));
    s.q().emit(result());
    await until(() => s.session.running.size === 1);
    s.session.tick();
    await tick();
    expect(s.q().received.length).toBe(1); // no nudge
  });
});

describe("context rollover (VFG_ORCH_MAX_CONTEXT, default 70000)", () => {
  const big = (n: number) => ({ type: "assistant", parent_tool_use_id: null,
    message: { content: [], usage: { input_tokens: 1000, cache_read_input_tokens: n - 1500, cache_creation_input_tokens: 500 } } });
  const opus = (input: number, cost: number) => ({ "claude-opus-4-8": { inputTokens: input, outputTokens: 10, cacheReadInputTokens: 0, cacheCreationInputTokens: 0, costUSD: cost } });

  function history(s: ReturnType<typeof setup>) {
    s.store.add("T1", { at: "2026-10-08T06:00:00.000Z", task: "make it day", context: "" });
    s.store.reply("T1", "done", "It's day now: time set to 1000.");
    s.store.add("T2", { at: "2026-10-08T06:01:00.000Z", task: "rain TNT", context: "" });
    s.store.reply("T2", "done", "TNT rain is running on everyone; stop it with vfg script stop tnt_rain.");
    s.store.add("T3", { at: "2026-10-08T06:02:00.000Z", task: "build a hut next to Steve", context: "Steve is at 120 64 -40." });
    s.store.reply("T3", "progress", "Working on the hut.");
  }

  test("over budget at an idle point: a fresh session with the handoff; the open task survives and is answered once", async () => {
    const s = setup(undefined, "hybrid");
    history(s);
    s.store.state.sessionId = "S1";
    await s.session.open("S1");
    const keeper = s.session.keepAlive(0);
    s.q().emit(big(71_000));
    s.q().emit(result({ modelUsage: opus(100, 0.5) }));
    await until(() => s.queries.length === 2);
    expect(s.queries[0]!.ended).toBe(true);
    expect(s.queries[1]!.options.resume).toBeUndefined();
    expect(s.session.rollovers).toBe(1);
    expect(s.store.get("T3")!.status).toBe("open");
    await until(() => s.queries[1]!.received.length === 1);
    const h = s.queries[1]!.received[0]!;
    expect(h).toStartWith("RUNNER: this is a fresh session: the last one had grown to 71000 tokens of context.");
    expect(h).toContain("Open tasks, each still needing exactly one done or failed reply:\n- T3 (sent 06:02:00 UTC): build a hut next to Steve Context: Steve is at 120 64 -40. Said so far: Working on the hut.");
    expect(h).toContain("Implementers running: none");
    expect(h).toContain("- T2 done: TNT rain is running on everyone; stop it with vfg script stop tnt_rain.");
    expect(h).toContain("- T1 done: It's day now");
    expect(h.indexOf("T1 done")).toBeLessThan(h.indexOf("T2 done"));
    // the new session answers the open task, exactly once
    s.queries[1]!.emit({ type: "system", subtype: "init", session_id: "S2", model: "opus", permissionMode: "dontAsk", tools: [] });
    await until(() => s.store.state.sessionId === "S2");
    expect(s.store.reply("T3", "done", "The hut is built.").ok).toBe(true);
    expect(s.store.reply("T3", "done", "again").ok).toBe(false);
    // usage: the old session's row went out before its snapshot was dropped; the new one counts from zero
    s.queries[1]!.emit(result({ modelUsage: opus(30, 0.1) }));
    await until(() => s.fake.calls().filter((c) => c[0] === "usage").length === 2);
    await s.session.settled();
    const ins = s.fake.calls().filter((c) => c[0] === "usage").map((c) => c[c.indexOf("--in") + 1]);
    expect(ins).toEqual(["100", "30"]);
    s.session.stopping = true;
    s.session.closeQuery();
    await keeper;
  });

  test("not while an implementer runs: it rolls over once the implementer has reported", async () => {
    const s = setup(undefined, "hybrid");
    history(s);
    await s.session.open();
    const keeper = s.session.keepAlive(0);
    s.q().emit(started("k1", "T3 hut", "u1"));
    s.q().emit(big(90_000));
    s.q().emit(result());
    await until(() => s.session.running.size === 1 && !s.session.busy);
    s.session.tick();
    await tick();
    expect(s.queries.length).toBe(1);
    s.q().emit(notified("k1"));
    await until(() => s.session.running.size === 0);
    s.advance(61_000);
    s.session.tick();
    await until(() => s.queries.length === 2);
    expect(s.store.get("T3")!.status).toBe("open");
    s.session.stopping = true;
    s.session.closeQuery();
    await keeper;
  });

  test("under the budget, or with the budget off: no rollover", async () => {
    for (const [extra, n] of [[{}, 69_000], [{ VFG_ORCH_MAX_CONTEXT: "0" }, 500_000]] as const) {
      const s = setup(undefined, "hybrid", extra as Record<string, string>);
      await s.session.open();
      s.q().emit(big(n));
      s.q().emit(result());
      await tick();
      s.session.tick();
      await tick();
      expect(s.queries.length).toBe(1);
      expect(s.session.contextTokens).toBe(n);
      s.session.closeQuery();
    }
  });

  test("a subagent's large context does not count; only the main thread's", async () => {
    const s = setup(undefined, "hybrid");
    await s.session.open();
    s.q().emit({ ...big(200_000), parent_tool_use_id: "toolu_1" });
    s.q().emit(result());
    await tick();
    expect(s.session.contextTokens).toBe(0);
    expect(s.queries.length).toBe(1);
    s.session.closeQuery();
  });

  test("a task waiting during the rollover goes in after the handoff", async () => {
    const s = setup(undefined, "hybrid");
    await s.session.open();
    const keeper = s.session.keepAlive(0);
    const task = (id: string) => `TASK ${id} at 00:00:00 UTC\ndo ${id}`;
    s.open("T4");
    s.open("T5");
    s.session.pushTask("T4", task("T4"));
    await until(() => s.q().received.length === 1);
    s.session.pushTask("T5", task("T5")); // waits: the session is busy with T4
    s.store.reply("T4", "done", "rain");
    s.q().emit(big(80_000));
    s.q().emit(result());
    await until(() => s.queries.length === 2);
    await until(() => s.queries[1]!.received.length === 1);
    expect(s.queries[1]!.received[0]).toContain("1 more task(s) are waiting");
    expect(s.queries[1]!.received[0]).toContain("Open tasks: none.");
    s.queries[1]!.emit(result()); // the handoff's turn
    await until(() => s.queries[1]!.received.length === 2);
    expect(s.queries[1]!.received[1]).toBe(task("T5"));
    s.session.stopping = true;
    s.session.closeQuery();
    await keeper;
  });
});

describe("options", () => {
  test("direct mode: no Agent, no implementer, the implementer's tools under the guard, low effort", () => {
    const dir = tempDir();
    const cfg = testConfig(dir, { VFG_ORCH_MODE: "direct" });
    expect(cfg.mode).toBe("direct");
    expect(cfg.model).toBe("opus");
    expect(cfg.effort).toBe("low");
    const o = buildOptions({
      cfg, store: newStore(dir), prompts: { orchestrator: "direct %REPO%", implementer: "" },
      guard: { repo: cfg.repo, maxImplementers: 4, implementerModel: "haiku", mainThreadRule: false, openTaskIds: () => [], activeImplementers: () => 0, onAgentAllowed: () => {} },
    });
    expect(o.model).toBe("opus");
    expect(o.effort).toBe("low");
    expect(o.tools).toEqual(["Bash", "Read", "Write", "Edit", "Glob", "Grep"]);
    expect(o.agents).toBeUndefined();
    expect(o.allowedTools).toEqual(rules(cfg.repo, "direct"));
    expect(o.allowedTools).not.toContain("Agent");
    expect(o.allowedTools).not.toContain("TaskStop");
    expect(o.allowedTools).toContain("mcp__orch__reply");
    expect(o.allowedTools).toContain("Bash(vfg cmd *)");
    expect(o.allowedTools).toContain(`Write(/${cfg.repo}/scripts/**)`);
    expect(o.systemPrompt).toBe(`direct ${cfg.repo}`);
    expect(o.permissionMode).toBe("dontAsk");
    const over = testConfig(dir, { VFG_ORCH_MODE: "direct", VFG_ORCH_MODEL: "haiku", VFG_ORCH_EFFORT: "medium" });
    expect([over.model, over.effort]).toEqual(["haiku", "medium"]);
  });

  test("defaults: hybrid mode, Opus low, Sonnet implementers at low; all overridable", () => {
    const dir = tempDir();
    const d = testConfig(dir);
    expect([d.mode, d.model, d.effort, d.implementerModel, d.implementerEffort]).toEqual(["hybrid", "opus", "low", "sonnet", "low"]);
    expect(testConfig(dir, { VFG_ORCH_MODE: "implementers" }).mode).toBe("implementers");
    expect(testConfig(dir, { VFG_ORCH_MODE: "direct" }).mode).toBe("direct");
    expect(testConfig(dir, { VFG_ORCH_MODE: "nonsense" }).mode).toBe("hybrid");
    const o = testConfig(dir, { VFG_IMPL_MODEL: "haiku", VFG_IMPL_EFFORT: "medium", VFG_ORCH_EFFORT: "high" });
    expect([o.implementerModel, o.implementerEffort, o.effort]).toEqual(["haiku", "medium", "high"]);
    expect(testConfig(dir, { VFG_IMPLEMENTER_MODEL: "haiku" }).implementerModel).toBe("haiku"); // the older name still works
  });

  test("hybrid mode: Agent and Bash, no Write or Edit for the session; implementers as before", () => {
    const dir = tempDir();
    const cfg = testConfig(dir);
    const o = buildOptions({
      cfg, store: newStore(dir), prompts: { orchestrator: "hybrid %REPO% %MAX_IMPLEMENTERS%", implementer: "impl" },
      guard: { repo: cfg.repo, maxImplementers: 4, implementerModel: "sonnet", mainThreadRule: true, hybrid: true, openTaskIds: () => [], activeImplementers: () => 0, onAgentAllowed: () => {} },
    });
    expect([o.model, o.effort]).toEqual(["opus", "low"]);
    expect(o.tools).toContain("Agent");
    expect(o.tools).toContain("Bash");
    expect(o.allowedTools).toEqual(rules(cfg.repo, "hybrid"));
    expect(o.allowedTools).toContain("Agent");
    expect(o.allowedTools).toContain("Bash(vfg cmd *)");
    expect(o.agents!.implementer!.model).toBe("sonnet");
    expect(o.agents!.implementer!.effort).toBe("low");
    expect(o.systemPrompt).toBe(`hybrid ${cfg.repo} 4`);
  });

  test("the session's guard holds the direct main thread to the implementer's rules", async () => {
    const s = setup(undefined, "direct");
    expect(s.session.guardContext().mainThreadRule).toBe(false);
    const s2 = setup(undefined, "implementers");
    expect(s2.session.guardContext().mainThreadRule).toBe(true);
    expect(s2.session.guardContext().hybrid).toBe(false);
    const s3 = setup(undefined, "hybrid");
    expect([s3.session.guardContext().mainThreadRule, s3.session.guardContext().hybrid]).toEqual([true, true]);
  });

  test("the CLI's environment leaves out keys and the parent session", () => {
    const env = sessionEnv({
      PATH: "/bin", HOME: "/h", VFG_REPO: "/r", ANTHROPIC_API_KEY: "k", ANTHROPIC_AUTH_TOKEN: "t", ANTHROPIC_BASE_URL: "http://proxy",
      CLAUDECODE: "1", CLAUDE_CODE_ENTRYPOINT: "cli", CLAUDE_CODE_SESSION_ID: "s", CLAUDE_CODE_SSE_PORT: "1", CLAUDE_PID: "9",
      CLAUDE_CODE_OAUTH_TOKEN: "subscription-token", CLAUDE_EFFORT: "low",
    });
    expect(Object.keys(env).sort()).toEqual(["CLAUDE_CODE_OAUTH_TOKEN", "HOME", "PATH", "VFG_REPO"]);
  });

  test("implementers mode: the session's options", () => {
    const dir = tempDir();
    process.env.ANTHROPIC_API_KEY = "sk-test-not-real";
    const cfg = testConfig(dir, { VFG_ORCH_MODE: "implementers", VFG_ORCH_MODEL: "haiku", VFG_ORCH_MAX_IMPLEMENTERS: "3" });
    const o = buildOptions({
      cfg, store: newStore(dir), resume: "S9",
      prompts: { orchestrator: "repo %REPO% max %MAX_IMPLEMENTERS%", implementer: "impl %REPO%" },
      guard: { repo: cfg.repo, maxImplementers: 3, implementerModel: "haiku", mainThreadRule: true, openTaskIds: () => [], activeImplementers: () => 0, onAgentAllowed: () => {} },
    });
    delete process.env.ANTHROPIC_API_KEY;
    expect(o.model).toBe("haiku"); // VFG_ORCH_MODEL wins
    expect(o.effort).toBe("low"); // the orchestrator's default
    expect(o.systemPrompt).toBe(`repo ${cfg.repo} max 3`);
    expect(o.cwd).toBe(cfg.repo);
    expect(o.settingSources).toEqual([]);
    expect(o.skills).toEqual([]);
    expect(o.strictMcpConfig).toBe(true);
    expect(Object.keys(o.mcpServers!)).toEqual(["orch"]);
    expect(o.tools).toEqual(["Agent", "TaskStop", "Bash", "Read", "Write", "Edit", "Glob", "Grep"]);
    expect(o.permissionMode).toBe("dontAsk");
    expect(o.resume).toBe("S9");
    expect(o.env!.ANTHROPIC_API_KEY).toBeUndefined();
    expect("ANTHROPIC_API_KEY" in o.env!).toBe(false);
    expect(o.env!.CLAUDE_AGENT_SDK_CLIENT_APP).toBe("vfg-orchestrator/0.1");
    const impl = o.agents!.implementer!;
    expect(impl.model).toBe("sonnet"); // VFG_IMPL_MODEL's default
    expect(impl.effort).toBe("low"); // VFG_IMPL_EFFORT's default, set on the agent definition
    expect(impl.tools).toEqual(["Bash", "Read", "Write", "Edit", "Glob", "Grep"]);
    expect(impl.background).toBe(true);
    expect(impl.maxTurns).toBe(40);
    expect(impl.prompt).toBe(`impl ${cfg.repo}`);
    expect(o.allowedTools).toEqual(rules(cfg.repo));
    expect(o.allowedTools).toContain(`Read(/${cfg.repo}/**)`);
    expect(o.allowedTools).toContain(`Write(/${cfg.repo}/scripts/**)`);
    expect(`Read(/${cfg.repo}/**)`.startsWith("Read(//")).toBe(true);
    expect(o.allowedTools!.some((r) => r.includes("vfg say"))).toBe(false);
  });
});
