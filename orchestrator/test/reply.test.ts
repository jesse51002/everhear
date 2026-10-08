import { describe, expect, test } from "bun:test";
import { writeFileSync } from "node:fs";
import { createReplyServer, handleReply, Outbox } from "../src/reply";
import { cleanText } from "../src/tasks";
import { vfg } from "../src/vfg";
import { newStore, tempDir, until, useFakeVfg } from "./helpers";

function storeWith(...ids: string[]) {
  const dir = tempDir();
  const store = newStore(dir);
  for (const id of ids) store.add(id, { at: new Date().toISOString(), task: "t", context: "" });
  store.save();
  return { dir, store };
}

describe("reply tool", () => {
  test("an unknown task is an error", () => {
    const { store } = storeWith("T1");
    const r = handleReply(store, { task: "T9", status: "done", text: "x" });
    expect(r.isError).toBe(true);
    expect(r.content[0]!.text).toContain("T9");
  });

  test("done closes; a second close is refused", () => {
    const { store } = storeWith("T7");
    expect(handleReply(store, { task: "T7", status: "done", text: "Day now." }).isError).toBeUndefined();
    expect(store.get("T7")!.status).toBe("done");
    const again = handleReply(store, { task: "T7", status: "failed", text: "oops" });
    expect(again.isError).toBe(true);
    expect(again.content[0]!.text).toBe("T7 is already closed as done.");
    expect(store.get("T7")!.replies.length).toBe(1);
  });

  test("progress after done is refused; progress before is kept", () => {
    const { store } = storeWith("T1");
    expect(handleReply(store, { task: "T1", status: "progress", text: "half way" }).isError).toBeUndefined();
    expect(store.get("T1")!.status).toBe("open");
    expect(handleReply(store, { task: "T1", status: "failed", text: "no luck" }).isError).toBeUndefined();
    expect(handleReply(store, { task: "T1", status: "progress", text: "more" }).isError).toBe(true);
    expect(store.get("T1")!.replies.map((r) => r.status)).toEqual(["progress", "failed"]);
  });

  test("text is cut to 300 with an ellipsis, newlines become spaces", () => {
    const { store } = storeWith("T1");
    handleReply(store, { task: "T1", status: "done", text: "a\nb\r\n" + "x".repeat(400) });
    const text = store.get("T1")!.replies[0]!.text;
    expect(text.length).toBe(300);
    expect(text.endsWith("…")).toBe(true);
    expect(text.startsWith("a b x")).toBe(true);
    expect(cleanText("short")).toBe("short");
    expect(cleanText("y".repeat(300))).toBe("y".repeat(300));
  });

  test("empty text is refused", () => {
    const { store } = storeWith("T1");
    expect(handleReply(store, { task: "T1", status: "done", text: " \n " }).isError).toBe(true);
    expect(store.get("T1")!.status).toBe("open");
  });

  test("the reply is saved in state.json at once", () => {
    const { store, dir } = storeWith("T1");
    handleReply(store, { task: "T1", status: "done", text: "ok" });
    const again = newStore(dir);
    expect(again.get("T1")!.status).toBe("done");
    expect(again.get("T1")!.replies[0]!.sent).toBe(false);
  });

  test("the MCP server is named orch and always loaded", () => {
    const { store } = storeWith();
    const s = createReplyServer(store) as any;
    expect(s.type).toBe("sdk");
    expect(s.name).toBe("orch");
  });
});

describe("outbox", () => {
  test("sends in order, with an argv array", async () => {
    const { store, dir } = storeWith("T1", "T2");
    const fake = useFakeVfg(dir);
    store.reply("T1", "progress", "building; it's 'quoted' $HOME");
    store.reply("T2", "done", "day now");
    store.reply("T1", "done", "castle built");
    const ob = new Outbox(store, vfg, [5, 5], 5);
    ob.start();
    await until(() => ob.pending() === 0);
    await ob.stop();
    expect(fake.calls()).toEqual([
      ["orch", "reply", "T1", "progress", "building; it's 'quoted' $HOME"],
      ["orch", "reply", "T2", "done", "day now"],
      ["orch", "reply", "T1", "done", "castle built"],
    ]);
    expect(newStore(dir).unsent()).toEqual([]);
  });

  test("a reply is retried after two failures and not dropped", async () => {
    const { store, dir } = storeWith("T1", "T2");
    const fake = useFakeVfg(dir);
    writeFileSync(fake.failFile, "2");
    store.reply("T1", "done", "first");
    store.reply("T2", "failed", "second");
    const ob = new Outbox(store, vfg, [20, 20], 20);
    ob.start();
    await until(() => ob.pending() === 0, 5000);
    await ob.stop();
    const calls = fake.calls().map((c) => c.slice(2, 4).join(" "));
    expect(calls).toEqual(["T1 done", "T1 done", "T1 done", "T2 failed"]);
  });

  test("unsent replies are sent after a restart", async () => {
    const { store, dir } = storeWith("T1");
    const fake = useFakeVfg(dir);
    writeFileSync(fake.failFile, "100");
    store.reply("T1", "done", "it worked");
    const ob = new Outbox(store, vfg, [10], 10);
    ob.start();
    await until(() => fake.calls().length >= 2);
    await ob.stop();
    expect(newStore(dir).unsent().length).toBe(1);
    // restart: a new process loads state.json, EC2 is back
    writeFileSync(fake.failFile, "0");
    const store2 = newStore(dir);
    const ob2 = new Outbox(store2, vfg, [10], 10);
    ob2.start();
    await until(() => ob2.pending() === 0);
    await ob2.stop();
    expect(newStore(dir).get("T1")!.replies[0]!.sent).toBe(true);
    expect(fake.calls().at(-1)).toEqual(["orch", "reply", "T1", "done", "it worked"]);
  });

  test("drain gives up at its deadline and keeps the reply", async () => {
    const { store, dir } = storeWith("T1");
    const fake = useFakeVfg(dir);
    writeFileSync(fake.failFile, "100");
    store.reply("T1", "failed", "stopped");
    const ob = new Outbox(store, vfg, [50], 50);
    ob.start();
    const t = Date.now();
    expect(await ob.drain(300)).toBe(false);
    expect(Date.now() - t).toBeLessThan(1000);
    await ob.stop();
    expect(newStore(dir).unsent().length).toBe(1);
  });
});
