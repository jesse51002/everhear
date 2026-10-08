// Task states and state.json, rewritten whole (temp file, then rename) after every change.
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { log } from "./config";

export type TaskStatus = "open" | "done" | "failed";
export type ReplyStatus = "progress" | "done" | "failed";

export interface Reply {
  status: ReplyStatus;
  text: string;
  sent: boolean;
  /** Global order of replies, for the outbox. */
  n: number;
}

export interface Task {
  at: string;
  task: string;
  context: string;
  seenAt: string;
  status: TaskStatus;
  nudged: boolean;
  replies: Reply[];
  closedAt?: string;
}

export interface ModelTotals {
  in: number;
  out: number;
  cost: number;
}

export interface State {
  sessionId?: string;
  inboxOffset: number;
  usage: Record<string, ModelTotals>;
  tasks: Record<string, Task>;
  nextReply: number;
}

export const MAX_TEXT = 300;

/** Newlines to spaces, trimmed, cut to 300 characters with "…". */
export function cleanText(text: string): string {
  const t = String(text ?? "").replace(/[\r\n]+/g, " ").trim();
  return t.length > MAX_TEXT ? t.slice(0, MAX_TEXT - 1) + "…" : t;
}

export type ReplyOutcome = { ok: true; reply: Reply } | { ok: false; error: string };

export class Store {
  state: State = Store.empty();
  /** Called after a reply is recorded (the outbox listens). */
  onReply: () => void = () => {};

  constructor(readonly file: string) {}

  static empty(): State {
    return { inboxOffset: 0, usage: {}, tasks: {}, nextReply: 1 };
  }

  load(): void {
    if (!existsSync(this.file)) {
      this.state = Store.empty();
      return;
    }
    try {
      const raw = JSON.parse(readFileSync(this.file, "utf8"));
      this.state = { ...Store.empty(), ...raw };
      this.state.tasks ??= {};
      this.state.usage ??= {};
      let max = 0;
      for (const t of Object.values(this.state.tasks)) {
        t.replies ??= [];
        for (const r of t.replies) max = Math.max(max, r.n ?? 0);
      }
      this.state.nextReply = Math.max(this.state.nextReply ?? 1, max + 1);
    } catch (e) {
      log("state", `state.json unreadable, starting empty: ${(e as Error).message}`);
      this.state = Store.empty();
    }
  }

  save(): void {
    mkdirSync(dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp-${process.pid}`;
    writeFileSync(tmp, JSON.stringify(this.state, null, 2) + "\n");
    renameSync(tmp, this.file);
  }

  /** Drop tasks closed more than `hours` ago whose replies are all sent. */
  dropClosedBefore(hours: number, now = Date.now()): string[] {
    const dropped: string[] = [];
    for (const [id, t] of Object.entries(this.state.tasks)) {
      if (t.status === "open") continue;
      const closed = Date.parse(t.closedAt ?? t.seenAt ?? t.at);
      if (Number.isFinite(closed) && now - closed > hours * 3600_000 && t.replies.every((r) => r.sent)) {
        delete this.state.tasks[id];
        dropped.push(id);
      }
    }
    return dropped;
  }

  get(id: string): Task | undefined {
    return this.state.tasks[id];
  }

  has(id: string): boolean {
    return id in this.state.tasks;
  }

  openIds(): string[] {
    return Object.entries(this.state.tasks)
      .filter(([, t]) => t.status === "open")
      .map(([id]) => id)
      .sort(byTaskNumber);
  }

  /** Records a task as open. */
  add(id: string, fields: { at: string; task: string; context: string }, now = new Date()): Task {
    const t: Task = {
      at: fields.at,
      task: fields.task,
      context: fields.context,
      seenAt: now.toISOString(),
      status: "open",
      nudged: false,
      replies: [],
    };
    this.state.tasks[id] = t;
    return t;
  }

  /**
   * The one path every answer takes, the model's and the runner's: it refuses an
   * unknown task, a closed one, and empty text; records the reply; closes the task
   * on done or failed. Exactly once is held here.
   */
  reply(id: string, status: ReplyStatus, text: string, now = new Date()): ReplyOutcome {
    const t = this.state.tasks[id];
    if (!t) return { ok: false, error: `${id} is not a task I know.` };
    if (t.status !== "open") return { ok: false, error: `${id} is already closed as ${t.status}.` };
    const clean = cleanText(text);
    if (!clean) return { ok: false, error: "The text is empty." };
    const reply: Reply = { status, text: clean, sent: false, n: this.state.nextReply++ };
    t.replies.push(reply);
    if (status !== "progress") {
      t.status = status;
      t.closedAt = now.toISOString();
    }
    this.save();
    this.onReply();
    return { ok: true, reply };
  }

  /** The runner closes a task as failed; nothing happens when it is already closed. */
  fail(id: string, text: string): boolean {
    return this.reply(id, "failed", text).ok;
  }

  /** Every unsent reply, in the order they were made. */
  unsent(): Array<{ id: string; reply: Reply }> {
    const out: Array<{ id: string; reply: Reply }> = [];
    for (const [id, t] of Object.entries(this.state.tasks)) {
      for (const r of t.replies) if (!r.sent) out.push({ id, reply: r });
    }
    return out.sort((a, b) => a.reply.n - b.reply.n);
  }
}

export function byTaskNumber(a: string, b: string): number {
  const na = Number(a.replace(/^\D+/, "")),
    nb = Number(b.replace(/^\D+/, ""));
  return Number.isFinite(na) && Number.isFinite(nb) && na !== nb ? na - nb : a.localeCompare(b);
}
