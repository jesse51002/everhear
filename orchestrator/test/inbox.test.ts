import { describe, expect, test } from "bun:test";
import { appendFileSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { InboxFollower, taskMessage } from "../src/inbox";
import { newStore, tempDir } from "./helpers";

function setup(staleMinutes = 10, now?: () => Date) {
  const dir = tempDir();
  const inbox = join(dir, "inbox.jsonl");
  const store = newStore(dir);
  const pushed: Array<[string, string]> = [];
  const f = new InboxFollower({ inbox, store, staleMinutes, onTask: (id, m) => pushed.push([id, m]), now });
  return { dir, inbox, store, pushed, f };
}

const line = (o: object) => JSON.stringify(o) + "\n";
const recent = () => new Date(Date.now() - 5_000).toISOString();

describe("inbox follower", () => {
  test("a missing inbox is empty", () => {
    const { f, pushed, store } = setup();
    expect(f.poll()).toEqual([]);
    expect(pushed).toEqual([]);
    expect(store.state.inboxOffset).toBe(0);
  });

  test("a half-written line waits for its newline", () => {
    const { f, inbox, pushed, store } = setup();
    const full = line({ id: "T1", at: recent(), task: "make it day", context: "" });
    writeFileSync(inbox, full.slice(0, 20));
    expect(f.poll()).toEqual([]);
    expect(store.state.inboxOffset).toBe(0);
    appendFileSync(inbox, full.slice(20));
    expect(f.poll()).toEqual(["T1"]);
    expect(pushed.map((p) => p[0])).toEqual(["T1"]);
    expect(store.state.inboxOffset).toBe(Buffer.byteLength(full));
    expect(store.get("T1")!.status).toBe("open");
  });

  test("whole lines before a half one are taken, the half one later", () => {
    const { f, inbox, store } = setup();
    const a = line({ id: "T1", at: recent(), task: "a" });
    const b = line({ id: "T2", at: recent(), task: "b ü" });
    writeFileSync(inbox, a + b.slice(0, 10));
    expect(f.poll()).toEqual(["T1"]);
    appendFileSync(inbox, b.slice(10));
    expect(f.poll()).toEqual(["T2"]);
    expect(store.state.inboxOffset).toBe(Buffer.byteLength(a + b));
    expect(store.get("T2")!.task).toBe("b ü");
  });

  test("a duplicate id is skipped, also after a restart", () => {
    const { f, inbox, pushed, dir } = setup();
    writeFileSync(inbox, line({ id: "T1", at: recent(), task: "a" }) + line({ id: "T1", at: recent(), task: "again" }));
    expect(f.poll()).toEqual(["T1"]);
    expect(pushed.length).toBe(1);
    // a restart: the state is reloaded; the inbox is read again from 0
    const store2 = newStore(dir);
    store2.state.inboxOffset = 0;
    const pushed2: string[] = [];
    new InboxFollower({ inbox, store: store2, staleMinutes: 10, onTask: (id) => pushed2.push(id) }).poll();
    expect(pushed2).toEqual([]);
  });

  test("a shrunk inbox is read again from 0", () => {
    const { f, inbox, store } = setup();
    writeFileSync(inbox, line({ id: "T1", at: recent(), task: "a long first task text" }) + line({ id: "T2", at: recent(), task: "b" }));
    f.poll();
    writeFileSync(inbox, line({ id: "T3", at: recent(), task: "c" }));
    expect(f.poll()).toEqual(["T3"]);
    expect(store.state.inboxOffset).toBe(readFileSync(inbox).length);
  });

  test("bad JSON and lines without an id are skipped", () => {
    const { f, inbox, store } = setup();
    writeFileSync(inbox, "{not json\n" + line({ task: "no id" }) + line({ id: "T4", at: recent(), task: "ok" }));
    expect(f.poll()).toEqual(["T4"]);
    expect(Object.keys(store.state.tasks)).toEqual(["T4"]);
  });

  test("an id without a task is failed as malformed", () => {
    const { f, inbox, store, pushed } = setup();
    writeFileSync(inbox, line({ id: "T5", at: recent() }) + line({ id: "T6", at: recent(), task: "   " }));
    expect(f.poll()).toEqual([]);
    expect(pushed).toEqual([]);
    for (const id of ["T5", "T6"]) {
      const t = store.get(id)!;
      expect(t.status).toBe("failed");
      expect(t.replies).toEqual([{ status: "failed", text: "malformed task", sent: false, n: expect.any(Number) }]);
    }
  });

  test("a stale task is failed as expired", () => {
    const { f, inbox, store, pushed } = setup(10);
    writeFileSync(inbox, line({ id: "T7", at: new Date(Date.now() - 3600_000).toISOString(), task: "too late" }));
    expect(f.poll()).toEqual([]);
    expect(pushed).toEqual([]);
    const t = store.get("T7")!;
    expect(t.status).toBe("failed");
    expect(t.replies[0]!.text).toBe("Expired: it waited 60 minutes for the orchestrator.");
  });

  test("the message carries id, time, task and context", () => {
    const { f, inbox, pushed } = setup(10, () => new Date("2026-10-07T18:22:40.000Z"));
    writeFileSync(inbox, line({ id: "T7", at: "2026-10-07T18:22:31.412Z", task: "build me a castle", context: "Steve is at 120 64 -40, near spawn. They want it big." }));
    f.poll();
    expect(pushed[0]![1]).toBe(
      "TASK T7 at 18:22:31 UTC\nbuild me a castle\n\nContext from the conversation:\nSteve is at 120 64 -40, near spawn. They want it big.",
    );
    expect(taskMessage("T1", new Date("2026-10-07T01:02:03Z"), "x", "")).toBe("TASK T1 at 01:02:03 UTC\nx");
  });

  test("the offset is saved in state.json", () => {
    const { f, inbox, dir } = setup();
    writeFileSync(inbox, line({ id: "T1", at: recent(), task: "a" }));
    f.poll();
    const saved = JSON.parse(readFileSync(join(dir, "state.json"), "utf8"));
    expect(saved.inboxOffset).toBe(readFileSync(inbox).length);
    expect(saved.tasks.T1.status).toBe("open");
  });

  test("start() follows appends", async () => {
    const { f, inbox, pushed } = setup();
    writeFileSync(inbox, "");
    f.start(20);
    appendFileSync(inbox, line({ id: "T9", at: recent(), task: "later" }));
    await new Promise((r) => setTimeout(r, 120));
    f.stop();
    expect(pushed.map((p) => p[0])).toEqual(["T9"]);
  });
});
