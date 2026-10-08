// The in-process MCP server "orch" with the reply tool, and the outbox that delivers replies.
import { createSdkMcpServer, tool } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import { log } from "./config";
import type { Store } from "./tasks";
import { type VfgRunner, why } from "./vfg";

export const REPLY_TOOL = "mcp__orch__reply";

export interface ReplyArgs {
  task: string;
  status: "progress" | "done" | "failed";
  text: string;
}

export type ToolResult = { content: Array<{ type: "text"; text: string }>; isError?: boolean };

/** The reply tool's logic, apart from the SDK so it can be tested. */
export function handleReply(store: Store, args: ReplyArgs): ToolResult {
  const r = store.reply(args.task, args.status, args.text);
  if (!r.ok) {
    log("reply", `refused ${args.task} ${args.status}: ${r.error}`);
    return { isError: true, content: [{ type: "text", text: r.error }] };
  }
  log("reply", `${args.task} ${args.status}: ${r.reply.text}`);
  const closed = args.status === "progress" ? "" : ` ${args.task} is now closed.`;
  return { content: [{ type: "text", text: `Recorded ${args.task} ${args.status}; it goes to the conversation.${closed}` }] };
}

export function createReplyServer(store: Store) {
  const reply = tool(
    "reply",
    'Answer a task; it is the only way anything you say reaches the conversation. status "done" or "failed" closes the task (exactly once per task); "progress" is optional, for tasks that clearly take over a minute, at most once a minute. text: one or two plain sentences, at most 300 characters.',
    {
      task: z.string().regex(/^T\d+$/).describe('The task id, like "T7".'),
      status: z.enum(["progress", "done", "failed"]),
      text: z.string().min(1).describe("What now exists or happened in the game, where, for whom, and how to stop it; or why it failed and what would work instead."),
    },
    async (args) => handleReply(store, args as ReplyArgs),
  );
  return createSdkMcpServer({ name: "orch", version: "0.1.0", tools: [reply], alwaysLoad: true });
}

const RETRY_MS = [2_000, 4_000, 8_000, 16_000];
const STEADY_MS = 30_000;

/**
 * Sends unsent replies in order, one `vfg orch reply <task> <status> <text>` each, retrying a
 * failure after 2, 4, 8, 16 and then every 30 s. It never drops one; unsent replies are in
 * state.json and survive a restart.
 */
export class Outbox {
  private running = false;
  private wake: (() => void) | null = null;
  private loop: Promise<void> | null = null;
  private failures = 0;
  private backoff = false;

  constructor(
    private readonly store: Store,
    private readonly run: VfgRunner,
    private readonly retryMs: number[] = RETRY_MS,
    private readonly steadyMs: number = STEADY_MS,
  ) {}

  start(): void {
    if (this.running) return;
    this.running = true;
    this.loop = this.work();
  }

  /** Something new to send: try now, unless waiting out a retry delay. */
  kick(): void {
    if (!this.backoff) this.wake?.();
  }

  async stop(): Promise<void> {
    this.running = false;
    this.wake?.();
    // A vfg call in flight may take a while; shutdown does not wait on it.
    await Promise.race([this.loop, sleep(1_000)]);
  }

  pending(): number {
    return this.store.unsent().length;
  }

  /** Waits until everything is sent or the time is up. True when nothing is left. */
  async drain(timeoutMs: number): Promise<boolean> {
    const end = Date.now() + timeoutMs;
    this.wake?.(); // shutting down: try at once, then on the usual schedule
    while (this.pending() > 0 && Date.now() < end) await sleep(50);
    return this.pending() === 0;
  }

  private sleepOrWake(ms: number): Promise<void> {
    return new Promise((res) => {
      const t = setTimeout(done, ms);
      const self = this;
      function done() {
        clearTimeout(t);
        if (self.wake === done) self.wake = null;
        res();
      }
      this.wake = done;
    });
  }

  private async work(): Promise<void> {
    while (this.running) {
      const next = this.store.unsent()[0];
      if (!next) {
        await this.sleepOrWake(1_000);
        continue;
      }
      const { id, reply } = next;
      const r = await this.run(["orch", "reply", id, reply.status, reply.text], 60_000);
      if (r.code === 0) {
        reply.sent = true;
        this.store.save();
        this.failures = 0;
        log("outbox", `sent ${id} ${reply.status}`);
        continue;
      }
      const delay = this.retryMs[this.failures] ?? this.steadyMs;
      this.failures++;
      log("outbox", `vfg orch reply ${id} ${reply.status} failed (${why(r)}); retrying in ${delay / 1000} s`);
      if (this.running) {
        this.backoff = true;
        await this.sleepOrWake(delay);
        this.backoff = false;
      }
    }
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}
