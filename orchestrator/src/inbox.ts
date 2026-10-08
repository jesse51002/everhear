// Follows inbox.jsonl from a byte offset: every 250 ms it compares the file's size with
// state.inboxOffset and reads only whole lines (up to the last newline).
import { closeSync, existsSync, openSync, readSync, statSync } from "node:fs";
import { hms, log } from "./config";
import type { Store } from "./tasks";

export interface InboxOptions {
  inbox: string;
  store: Store;
  staleMinutes: number;
  /** A task was recorded open: push it to the session. */
  onTask: (id: string, message: string) => void;
  now?: () => Date;
}

export function taskMessage(id: string, at: Date, task: string, context: string): string {
  let msg = `TASK ${id} at ${hms(at)} UTC\n${task.trim()}`;
  if (context.trim()) msg += `\n\nContext from the conversation:\n${context.trim()}`;
  return msg;
}

export class InboxFollower {
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly now: () => Date;

  constructor(private readonly o: InboxOptions) {
    this.now = o.now ?? (() => new Date());
  }

  start(intervalMs = 250): void {
    this.poll();
    this.timer = setInterval(() => this.poll(), intervalMs);
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** One pass. Returns the ids recorded open. */
  poll(): string[] {
    const { store, inbox } = this.o;
    let size = 0;
    try {
      size = existsSync(inbox) ? statSync(inbox).size : 0;
    } catch {
      size = 0;
    }
    let offset = store.state.inboxOffset ?? 0;
    if (size < offset) {
      log("inbox", `inbox is smaller than the offset (${size} < ${offset}); reading it again from 0`);
      offset = 0;
      store.state.inboxOffset = 0;
      store.save();
    }
    if (size === offset) return [];
    const buf = Buffer.alloc(size - offset);
    const fd = openSync(inbox, "r");
    let got = 0;
    try {
      got = readSync(fd, buf, 0, buf.length, offset);
    } finally {
      closeSync(fd);
    }
    const end = buf.subarray(0, got).lastIndexOf(0x0a);
    if (end < 0) return []; // a half-written line: wait for its newline
    const text = buf.subarray(0, end + 1).toString("utf8");
    store.state.inboxOffset = offset + end + 1;
    const opened: string[] = [];
    const pushes: Array<[string, string]> = [];
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      const r = this.take(line);
      if (r) {
        opened.push(r[0]);
        pushes.push(r);
      }
    }
    store.save();
    for (const [id, msg] of pushes) this.o.onTask(id, msg);
    return opened;
  }

  private take(line: string): [string, string] | null {
    const { store, staleMinutes } = this.o;
    let obj: any;
    try {
      obj = JSON.parse(line);
    } catch {
      log("inbox", `bad JSON, skipped: ${line.slice(0, 200)}`);
      return null;
    }
    if (!obj || typeof obj !== "object" || typeof obj.id !== "string" || !obj.id.trim()) {
      log("inbox", `no task id, skipped: ${line.slice(0, 200)}`);
      return null;
    }
    const id = obj.id.trim();
    if (store.has(id)) {
      log("inbox", `${id} is already known, skipped`);
      return null;
    }
    const now = this.now();
    const parsed = typeof obj.at === "string" ? Date.parse(obj.at) : NaN;
    const at = Number.isFinite(parsed) ? new Date(parsed) : now;
    const task = typeof obj.task === "string" ? obj.task : "";
    const context = typeof obj.context === "string" ? obj.context : "";
    store.add(id, { at: at.toISOString(), task, context }, now);
    if (!task.trim()) {
      log("inbox", `${id} has no task: failed as malformed`);
      store.fail(id, "malformed task");
      return null;
    }
    const waited = (now.getTime() - at.getTime()) / 60_000;
    if (waited > staleMinutes) {
      const n = Math.round(waited);
      log("inbox", `${id} waited ${n} minutes: failed as expired`);
      store.fail(id, `Expired: it waited ${n} minutes for the orchestrator.`);
      return null;
    }
    log("inbox", `${id} open: ${task.replace(/\s+/g, " ").slice(0, 120)}`);
    return [id, taskMessage(id, at, task, context)];
  }
}
