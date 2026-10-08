// The one query(): its options, the input queue that never ends, the message loop, the idle
// check that makes sure every task is answered, the watchdog, and resume and restart.
import {
  type Options,
  type Query,
  type SDKMessage,
  type SDKResultMessage,
  type SDKUserMessage,
  query as sdkQuery,
} from "@anthropic-ai/claude-agent-sdk";
import { join } from "node:path";
import { type Config, hms, log } from "./config";
import { brief, makeGuard, taskIdOf } from "./guard";
import { createReplyServer, REPLY_TOOL, type Outbox } from "./reply";
import type { Store } from "./tasks";
import type { UsageMeter } from "./usage";

export type QueryFn = (p: { prompt: AsyncIterable<SDKUserMessage>; options: Options }) => Query;

export class FatalError extends Error {}

export const MSG = {
  interrupted: "The orchestrator restarted while this was running; anything it started may be half done.",
  stopped: "The orchestrator was stopped.",
  noAnswer: "The orchestrator ended without an answer",
  couldNotRun: (err: string) => `The orchestrator could not run: ${err.slice(0, 120)}`,
  nudge: (ids: string[], mode: Mode = "implementers") =>
    `RUNNER: still open with nothing running: ${ids.join(", ")}. Reply done or failed for each now, or ${
      mode === "direct" ? "do what it needs" : mode === "hybrid" ? "do it or start the implementer it needs" : "start the implementer it needs"
    }.`,
  closed: (ids: string[]) => `RUNNER: closed ${ids.join(", ")} as failed.`,
  restarted: (at: Date, ids: string[], mode: Mode = "implementers") =>
    `RUNNER: restarted at ${hms(at)} UTC. Closed as failed: ${ids.join(", ")}. ${mode === "direct" ? "Work in progress was cut off; scripts started on the server may still be running." : "Implementers from before are gone; scripts they started on the server may still be running."}`,
  overtime: (min: number) => `RUNNER: this task ran over ${min} minutes and was stopped. Reply failed for it, saying how far it got.`,
};

type Mode = import("./config").Mode;
type AgentEffort = NonNullable<import("@anthropic-ai/claude-agent-sdk").AgentDefinition["effort"]>;

/** The allow rules; with permissionMode "dontAsk" everything else is denied without a prompt. */
export function rules(repo: string, mode: Mode = "implementers"): string[] {
  return [
    REPLY_TOOL, ...(mode !== "direct" ? ["Agent", "TaskStop"] : []),
    `Read(/${repo}/**)`, "Glob", "Grep",
    // hybrid: the session never writes; implementers (who run under these rules too) do
    `Edit(/${repo}/scripts/**)`, `Write(/${repo}/scripts/**)`,
    "Bash(vfg cmd *)",
    "Bash(vfg script deploy)", "Bash(vfg script deploy *)",
    "Bash(vfg script run *)", "Bash(vfg script stop *)", "Bash(vfg script logs *)",
    "Bash(vfg script list)",
    "Bash(vfg history)", "Bash(vfg history *)",
    "Bash(python3 -m py_compile *)",
    "Bash(sleep *)",
  ];
}

export interface Prompts {
  orchestrator: string;
  implementer: string;
}

export function fillPrompt(text: string, cfg: Config): string {
  return text.replaceAll("%REPO%", cfg.repo).replaceAll("%MAX_IMPLEMENTERS%", String(cfg.maxImplementers));
}

export interface OptionParts {
  cfg: Config;
  prompts: Prompts;
  store: Store;
  guard: Parameters<typeof makeGuard>[0];
  resume?: string;
  /** The permission probe runs the implementer as the main thread. */
  agent?: string;
  stderr?: (line: string) => void;
}

/**
 * The CLI's environment: the owner's subscription through the CLI's own login in ~/.claude, never
 * an API key; and nothing of the Claude session that started the runner (vfg orch start runs
 * from the conversation), whose variables describe that session, not this one.
 */
export function sessionEnv(from: Record<string, string | undefined>): Record<string, string | undefined> {
  const env: Record<string, string | undefined> = {};
  for (const [k, v] of Object.entries(from)) {
    if (k === "ANTHROPIC_API_KEY" || k === "ANTHROPIC_AUTH_TOKEN" || k === "ANTHROPIC_BASE_URL") continue;
    if (k === "CLAUDECODE" || k === "CLAUDE_PID" || k === "CLAUDE_EFFORT" || k === "CLAUDE_AGENT_SDK_VERSION") continue;
    if (k.startsWith("CLAUDE_CODE_") && k !== "CLAUDE_CODE_OAUTH_TOKEN") continue;
    env[k] = v;
  }
  return env;
}

/** The session's options. The permission probe builds its one-shot query with this too. */
export function buildOptions(p: OptionParts): Options {
  const { cfg } = p;
  const env = sessionEnv(process.env);
  env.CLAUDE_AGENT_SDK_CLIENT_APP = "vfg-orchestrator/0.1";
  env.PYTHONPYCACHEPREFIX = join(cfg.orchDir, "pycache"); // py_compile leaves no __pycache__ in scripts/
  const direct = cfg.mode === "direct";
  const opts: Options = {
    model: cfg.model,
    effort: cfg.effort as Options["effort"],
    systemPrompt: fillPrompt(p.prompts.orchestrator, cfg),
    cwd: cfg.repo,
    settingSources: [],
    skills: [],
    strictMcpConfig: true,
    mcpServers: { orch: createReplyServer(p.store) },
    // direct: the session works itself with the implementer's tools. hybrid: it runs simple commands
    // itself and briefs implementers; it has no Write or Edit (the guard refuses them on the main
    // thread). Write and Edit stay in the list so implementers can have them.
    tools: direct
      ? ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
      : ["Agent", "TaskStop", "Bash", "Read", "Write", "Edit", "Glob", "Grep"],
    allowedTools: rules(cfg.repo, cfg.mode),
    permissionMode: "dontAsk",
    hooks: { PreToolUse: [{ hooks: [makeGuard(p.guard)] }] },
    env,
    stderr: p.stderr ?? ((line: string) => log("cli", line.trimEnd())),
  };
  if (!direct) {
    opts.agents = {
      implementer: {
        description: "Carries out one job on the Minecraft server with vfg commands and the scripts folder, then reports.",
        prompt: fillPrompt(p.prompts.implementer, cfg),
        model: cfg.implementerModel,
        effort: cfg.implementerEffort as AgentEffort,
        tools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        background: true,
        maxTurns: 40,
      },
    };
  }
  if (p.resume) opts.resume = p.resume;
  if (p.agent) opts.agent = p.agent;
  return opts;
}

/** The prompt: an async iterable fed from memory that never ends on its own. */
export class InputQueue {
  private buf: SDKUserMessage[] = [];
  private waiter: ((m: SDKUserMessage | null) => void) | null = null;
  private gen = 0;
  /** A message was handed to the session. */
  onPull: () => void = () => {};

  static message(text: string): SDKUserMessage {
    return { type: "user", message: { role: "user", content: text }, parent_tool_use_id: null };
  }

  push(text: string): void {
    const m = InputQueue.message(text);
    if (this.waiter) {
      const w = this.waiter;
      this.waiter = null;
      this.onPull();
      w(m);
    } else this.buf.push(m);
  }

  /** Ahead of anything already waiting. */
  unshift(text: string): void {
    const m = InputQueue.message(text);
    if (this.waiter) {
      const w = this.waiter;
      this.waiter = null;
      this.onPull();
      w(m);
    } else this.buf.unshift(m);
  }

  get length(): number {
    return this.buf.length;
  }

  /** A fresh iterable for one query. Whatever is still buffered goes to it. */
  attach(): AsyncIterable<SDKUserMessage> {
    const gen = ++this.gen;
    const self = this;
    return {
      [Symbol.asyncIterator]() {
        return {
          next(): Promise<IteratorResult<SDKUserMessage>> {
            if (gen !== self.gen) return Promise.resolve({ done: true, value: undefined });
            const m = self.buf.shift();
            if (m) {
              self.onPull();
              return Promise.resolve({ done: false, value: m });
            }
            return new Promise((res) => {
              self.waiter = (msg) => res(msg ? { done: false, value: msg } : { done: true, value: undefined });
            });
          },
          return(): Promise<IteratorResult<SDKUserMessage>> {
            if (gen === self.gen) self.detach();
            return Promise.resolve({ done: true, value: undefined });
          },
        };
      },
    };
  }

  /** Ends the current iterable (its query is gone). */
  detach(): void {
    this.gen++;
    const w = this.waiter;
    this.waiter = null;
    w?.(null);
  }
}

interface Implementer {
  task: string | null;
  description: string;
  started: number;
  stopping: boolean;
}

export interface SessionDeps {
  cfg: Config;
  store: Store;
  outbox: Outbox;
  usage: UsageMeter;
  prompts: Prompts;
  queryFn?: QueryFn;
  now?: () => number;
  /** Restart delays, for tests. */
  delays?: { first: number; max: number; healthy: number };
  /** An API key turned up after start: the process must exit 2. */
  onFatal?: (e: FatalError) => void;
}

export class Session {
  readonly input = new InputQueue();
  q: Query | null = null;
  readonly running = new Map<string, Implementer>(); // SDK task_id -> implementer
  readonly pending = new Map<string, { task: string; at: number }>(); // tool_use_id -> allowed, not started
  busy = false;
  sawResult = false;
  queuedTurns = 0;
  wakeDeadline = 0;
  lastResultError: string | null = null;
  turnsThisQuery = 0;
  stopping = false;
  private readonly now: () => number;
  private readonly queryFn: QueryFn;
  private loopDone: Promise<void> = Promise.resolve();
  private stderrTail: string[] = [];
  private usageChain: Promise<unknown> = Promise.resolve();
  private watchdog: ReturnType<typeof setInterval> | null = null;
  private wakeRestart: (() => void) | null = null;
  /** The main thread's context: the input of its last model call (fresh, cache-read and cache-written tokens). */
  contextTokens = 0;
  /** Context rollovers so far. */
  rollovers = 0;
  private rolloverAt: number | null = null;
  /** Direct mode: tasks waiting for the session to finish the one before. */
  readonly waiting: Array<{ id: string; message: string }> = [];
  private turnStartedAt = 0;
  private turnOvertime = false;

  constructor(private readonly d: SessionDeps) {
    this.now = d.now ?? Date.now;
    this.queryFn = d.queryFn ?? (sdkQuery as QueryFn);
    this.input.onPull = () => {
      if (!this.busy) {
        this.turnStartedAt = this.now();
        this.turnOvertime = false;
      }
      this.busy = true; // a turn is coming
    };
  }

  /** direct and hybrid: tasks go to the session one at a time, and a turn has a time limit. */
  get direct(): boolean {
    return this.d.cfg.mode !== "implementers";
  }

  push(text: string): void {
    log("session", `push: ${text.split("\n")[0]}`);
    this.input.push(text);
  }

  /**
   * A task from the inbox. Implementers mode: straight in, so it can fold into a running turn.
   * Direct and hybrid: one at a time; it waits until the session has finished the turn before.
   */
  pushTask(id: string, message: string): void {
    if (!this.direct) return this.push(message);
    this.waiting.push({ id, message });
    log("session", `queued ${id}; ${this.waiting.length} waiting`);
    this.deliver();
  }

  /** Direct mode: hand the next waiting task to the session when it is between turns. */
  deliver(): void {
    if (!this.direct || this.stopping || !this.q || !this.waiting.length) return;
    if (this.busy || this.queuedTurns > 0 || this.input.length > 0) return;
    while (this.waiting.length) {
      const next = this.waiting.shift()!;
      if (this.d.store.get(next.id)?.status !== "open") continue; // closed meanwhile
      this.push(next.message);
      return;
    }
  }

  guardContext() {
    const { cfg, store } = this.d;
    return {
      repo: cfg.repo,
      maxImplementers: cfg.maxImplementers,
      implementerModel: cfg.implementerModel,
      mainThreadRule: cfg.mode !== "direct", // direct: the main thread is held to the implementer's rules
      hybrid: cfg.mode === "hybrid", // hybrid: it orchestrates, and runs simple commands under the implementer's rules
      openTaskIds: () => store.openIds(),
      activeImplementers: () => this.running.size + this.pending.size,
      onAgentAllowed: (task: string, toolUseId: string) => {
        this.pending.set(toolUseId || `anon-${this.now()}`, { task, at: this.now() });
      },
    };
  }

  options(resume?: string): Options {
    return buildOptions({
      cfg: this.d.cfg,
      prompts: this.d.prompts,
      store: this.d.store,
      guard: this.guardContext(),
      resume,
      stderr: (line) => {
        const l = line.trimEnd();
        if (!l) return;
        this.stderrTail.push(l);
        if (this.stderrTail.length > 20) this.stderrTail.shift();
        log("cli", l);
      },
    });
  }

  /**
   * Starts one query, resuming `resume` if given, and checks the login: an API key is fatal.
   * Resolves once the query is up; the message loop runs on until it ends.
   */
  async open(resume?: string): Promise<void> {
    this.turnsThisQuery = 0;
    this.sawResult = false;
    this.busy = false;
    this.queuedTurns = 0;
    this.wakeDeadline = 0;
    this.running.clear();
    this.pending.clear();
    this.stderrTail = [];
    this.contextTokens = 0;
    const q = this.queryFn({ prompt: this.input.attach(), options: this.options(resume) });
    this.q = q;
    this.loopDone = this.loop(q);
    let info;
    try {
      info = await withTimeout(q.accountInfo(), 60_000);
    } catch (e) {
      // The loop sees the query end and the restart logic takes it from there; the
      // init message's apiKeySource is checked as well.
      log("session", `accountInfo failed: ${(e as Error).message}`);
      return;
    }
    const src = info?.apiKeySource;
    if (src && src !== "none") {
      throw new FatalError(`the CLI would use an API key (${src}), not the logged-in subscription; refusing to run`);
    }
    log("session", `logged in: subscription ${info?.subscriptionType ?? "unknown"}, model ${this.d.cfg.model}, implementers ${this.d.cfg.implementerModel}${resume ? ", resuming " + resume : ", new session"}`);
  }

  private async loop(q: Query): Promise<void> {
    try {
      for await (const msg of q) this.handle(msg);
      log("session", "the session ended");
    } catch (e) {
      if (!this.stopping) log("session", `the session broke: ${(e as Error).message}`);
    }
  }

  handle(msg: SDKMessage): void {
    const { store } = this.d;
    const sid = (msg as any).session_id;
    if (sid && sid !== store.state.sessionId && !(msg as any).parent_tool_use_id) {
      store.state.sessionId = sid;
      store.save();
      log("session", `session ${sid}`);
    }
    switch (msg.type) {
      case "assistant":
        this.busy = true;
        if (!msg.parent_tool_use_id) {
          this.logAssistant(msg);
          const u = (msg.message as any)?.usage;
          if (u) this.contextTokens = (u.input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0);
        }
        break;
      case "user":
      case "stream_event":
        this.busy = true;
        break;
      case "result":
        this.onResult(msg);
        break;
      case "rate_limit_event":
        log("session", `rate limit: ${brief(msg.rate_limit_info)}`);
        break;
      case "system":
        this.onSystem(msg as any);
        break;
    }
  }

  private logAssistant(msg: Extract<SDKMessage, { type: "assistant" }>): void {
    for (const b of (msg.message?.content ?? []) as any[]) {
      if (b.type === "tool_use") {
        const desc = b.name === "Agent" ? `${b.input?.description ?? ""}` : brief(b.input);
        log("orch", `${b.name} ${desc}`);
      } else if (b.type === "text" && b.text?.trim()) {
        log("orch", `says: ${b.text.trim().replace(/\s+/g, " ").slice(0, 300)}`);
      }
    }
  }

  private onSystem(msg: any): void {
    switch (msg.subtype) {
      case "init":
        log("session", `init: model ${msg.model}, permission mode ${msg.permissionMode}, tools ${(msg.tools ?? []).join(",")}`);
        if (msg.apiKeySource && msg.apiKeySource !== "none") {
          this.d.onFatal?.(new FatalError(`the CLI is using an API key (${msg.apiKeySource}), not the logged-in subscription`));
        }
        break;
      case "task_started": {
        if (!isImplementerTask(msg)) break;
        this.track(msg.task_id, msg.description, msg.tool_use_id);
        log("session", `implementer started: ${msg.description} (${msg.task_id}); ${this.running.size} running`);
        break;
      }
      case "task_notification": {
        const r = this.running.get(msg.task_id);
        this.untrack(msg.task_id);
        log("session", `implementer ${msg.status}: ${r?.description ?? msg.task_id}; ${this.running.size} running. ${String(msg.summary ?? "").replace(/\s+/g, " ").slice(0, 200)}`);
        break;
      }
      case "background_tasks_changed": {
        // Every live background task, replace semantics: the surest view of what is running.
        const live = new Map<string, any>();
        for (const t of msg.tasks ?? []) if (isImplementerTask(t) && !t.ambient) live.set(t.task_id, t);
        for (const id of [...this.running.keys()]) if (!live.has(id)) this.untrack(id);
        for (const [id, t] of live) {
          if (this.running.has(id)) continue;
          this.track(id, t.description);
          log("session", `implementer seen: ${t.description} (${id}); ${this.running.size} running`);
        }
        break;
      }
      case "task_updated": {
        const st = msg.patch?.status;
        if (st === "completed" || st === "failed" || st === "killed") this.untrack(msg.task_id);
        break;
      }
      case "api_retry":
        log("session", `API retry: ${brief(msg)}`);
        break;
    }
  }

  /** An implementer is running: count it, drop its allowance, clear its task's nudge. */
  private track(id: string, description: string, toolUseId?: string): void {
    const { store } = this.d;
    const task = taskIdOf(description);
    if (!this.running.has(id)) this.running.set(id, { task, description: description ?? "", started: this.now(), stopping: false });
    if (toolUseId && this.pending.has(toolUseId)) this.pending.delete(toolUseId);
    else {
      for (const [k, v] of this.pending) {
        if (v.task === task) {
          this.pending.delete(k);
          break;
        }
      }
    }
    const t = task ? store.get(task) : undefined;
    if (t && t.nudged) {
      t.nudged = false;
      store.save();
    }
    if (task) this.d.usage.note([task]);
  }

  /** An implementer ended. Its report starts a turn: give that a minute before calling it idle. */
  private untrack(id: string): void {
    if (!this.running.delete(id)) return;
    if (!this.busy) this.wakeDeadline = Math.max(this.wakeDeadline, this.now() + 60_000);
  }

  private onResult(msg: SDKResultMessage): void {
    this.busy = false;
    this.sawResult = true;
    this.wakeDeadline = 0;
    this.turnsThisQuery++;
    this.queuedTurns = msg.queued_turn_count ?? 0;
    const isError = msg.is_error || msg.subtype !== "success";
    this.lastResultError = isError ? resultError(msg) : null;
    log("session", `turn ${isError ? "error" : "done"} (${msg.subtype}, ${msg.num_turns} rounds, ${Math.round(msg.duration_ms / 1000)} s)${isError ? ": " + this.lastResultError : ""}`);
    for (const d of msg.permission_denials ?? []) log("denied", `${d.tool_name} ${brief(d.tool_input)}`);
    const mu = msg.modelUsage;
    this.usageChain = this.usageChain.then(() => this.d.usage.onResult(mu)).catch((e) => log("usage", (e as Error).message));
    if (this.maybeRollover()) return;
    this.deliver();
    this.idleCheck();
  }

  isIdle(): boolean {
    return (
      this.q !== null &&
      this.sawResult &&
      !this.busy &&
      this.queuedTurns === 0 &&
      this.input.length === 0 &&
      this.running.size === 0 &&
      this.pending.size === 0 &&
      this.now() >= this.wakeDeadline
    );
  }

  /** When idle with tasks still open: nudge each once; close those nudged before. */
  idleCheck(): void {
    if (this.stopping || !this.isIdle()) return;
    const { store } = this.d;
    const waiting = new Set(this.waiting.map((w) => w.id));
    const open = store.openIds().filter((id) => !waiting.has(id));
    if (!open.length) return;
    const toClose = open.filter((id) => store.get(id)!.nudged);
    const toNudge = open.filter((id) => !store.get(id)!.nudged);
    if (toClose.length) {
      const why = this.lastResultError ? MSG.couldNotRun(this.lastResultError) : MSG.noAnswer;
      for (const id of toClose) store.fail(id, why);
      log("session", `closed ${toClose.join(", ")} as failed: ${why}`);
      this.push(MSG.closed(toClose));
    }
    if (toNudge.length) {
      for (const id of toNudge) store.get(id)!.nudged = true;
      store.save();
      this.push(MSG.nudge(toNudge, this.d.cfg.mode));
    }
  }

  /** Every 10 s: stale allowances, the implementer (or direct turn) time limit, delivery, the idle check. */
  tick(): void {
    const now = this.now();
    const limitMin = this.d.cfg.implementerMinutes;
    if (this.direct && this.busy && !this.turnOvertime && this.turnStartedAt && now - this.turnStartedAt > limitMin * 60_000) {
      this.turnOvertime = true;
      log("session", `a turn ran over ${limitMin} min: interrupting it`);
      this.q?.interrupt().catch((e) => log("session", `interrupt failed: ${(e as Error).message}`));
      this.push(MSG.overtime(limitMin));
    }
    if (this.maybeRollover()) return;
    this.deliver();
    for (const [k, v] of this.pending) if (now - v.at > 60_000) this.pending.delete(k);
    const limit = this.d.cfg.implementerMinutes * 60_000;
    for (const [tid, imp] of this.running) {
      if (!imp.stopping && now - imp.started > limit) {
        imp.stopping = true;
        log("session", `implementer over ${this.d.cfg.implementerMinutes} min, stopping: ${imp.description}`);
        this.q?.stopTask(tid).catch((e) => log("session", `stopTask failed: ${(e as Error).message}`));
      }
    }
    this.idleCheck();
  }

  startWatchdog(ms = 10_000): void {
    this.watchdog ??= setInterval(() => this.tick(), ms);
  }

  /** Closes every open task as failed; returns their ids. Tasks still waiting to start stay, unless `all`. */
  closeOpen(text: string, all = false): string[] {
    const waiting = new Set(all ? [] : this.waiting.map((w) => w.id));
    const ids = this.d.store.openIds().filter((id) => !waiting.has(id));
    for (const id of ids) this.d.store.fail(id, text);
    return ids;
  }

  /**
   * Over the context budget and idle (between tasks, no turn running or queued, no implementer
   * running): close this session; keepAlive opens a fresh one and hands it what it needs.
   * Implementers live inside the session's process, so a rollover waits until none is running.
   */
  maybeRollover(): boolean {
    const max = this.d.cfg.maxContext;
    if (!max || this.stopping || this.rolloverAt !== null || !this.q || this.contextTokens <= max || !this.isIdle()) return false;
    this.rolloverAt = this.contextTokens;
    log("session", `rollover: the session's context reached ${this.contextTokens} tokens (budget ${max}); starting a fresh one`);
    this.closeQuery();
    return true;
  }

  /** What a fresh session needs from the last one, from the runner's own records. */
  handoff(tokens: number): string {
    const { store, cfg } = this.d;
    const clip = (t: string, n: number) => {
      const x = t.replace(/\s+/g, " ").trim();
      return x.length > n ? x.slice(0, n - 1) + "…" : x;
    };
    const waiting = new Set(this.waiting.map((w) => w.id));
    const open = store.openIds().filter((id) => !waiting.has(id));
    const lines = [`RUNNER: this is a fresh session: the last one had grown to ${tokens} tokens of context. What you need from it:`];
    lines.push(open.length ? "Open tasks, each still needing exactly one done or failed reply:" : "Open tasks: none.");
    for (const id of open) {
      const t = store.get(id)!;
      const last = t.replies.at(-1);
      lines.push(`- ${id} (sent ${hms(new Date(t.at))} UTC): ${clip(t.task, 300)}` +
        (t.context.trim() ? ` Context: ${clip(t.context, 300)}` : "") +
        (last ? ` Said so far: ${clip(last.text, 200)}` : ""));
    }
    if (cfg.mode !== "direct") lines.push("Implementers running: none (a fresh session starts only when none are).");
    const recent = Object.entries(store.state.tasks)
      .filter(([, t]) => t.status !== "open")
      .map(([id, t]) => ({ id, r: t.replies.filter((r) => r.status !== "progress").at(-1) }))
      .filter((x) => x.r)
      .sort((a, b) => a.r!.n - b.r!.n)
      .slice(-8);
    lines.push(recent.length
      ? "Recent answers, newest last (they say what was built or left running, and how to stop it):"
      : "Recent answers: none.");
    for (const { id, r } of recent) lines.push(`- ${id} ${r!.status}: ${clip(r!.text, 300)}`);
    if (this.waiting.length) lines.push(`${this.waiting.length} more task(s) are waiting; they come one at a time.`);
    return lines.join("\n");
  }

  /**
   * Keeps a session running for the life of the process: the first open() has already run;
   * when the session ends or throws, open tasks are failed as interrupted, and after 5 s
   * (doubling to 60 s, back to 5 s after 10 healthy minutes) it is started again with resume.
   */
  async keepAlive(firstStartedAt: number): Promise<void> {
    const delays = this.d.delays ?? { first: 5_000, max: 60_000, healthy: 600_000 };
    let delay = delays.first;
    let startedAt = firstStartedAt;
    let resumed = this.d.store.state.sessionId;
    while (!this.stopping) {
      await this.loopDone;
      this.input.detach();
      this.q = null;
      if (this.stopping) break;
      if (this.rolloverAt !== null) {
        // A context rollover, not a failure: nothing is closed. The old session's last usage rows
        // go out first, then its snapshot is dropped, since a new session's totals start at zero.
        const tokens = this.rolloverAt;
        const old = this.d.store.state.sessionId;
        await this.usageChain;
        this.forgetSession();
        startedAt = this.now();
        delay = delays.first;
        await this.open(undefined);
        this.rolloverAt = null;
        if (this.stopping) break;
        const handoff = this.handoff(tokens);
        this.input.unshift(handoff); // ahead of anything that came in meanwhile
        this.rollovers++;
        const open = this.d.store.openIds().filter((id) => !this.waiting.some((w) => w.id === id));
        log("session", `rollover #${this.rollovers} done: ${old ?? "the last session"} at ${tokens} tokens -> a fresh session; handoff ${handoff.length} characters, open tasks carried: ${open.join(", ") || "none"}`);
        resumed = undefined;
        this.deliver();
        continue;
      }
      const ranFor = this.now() - startedAt;
      const resumeFailed =
        !!resumed && this.turnsThisQuery === 0 && (ranFor < 30_000 || /no conversation found/i.test(this.stderrTail.join(" ")));
      const closed = this.closeOpen(MSG.interrupted);
      if (resumeFailed) {
        log("session", `resuming ${resumed} failed before any turn ran: starting a new session`);
        this.forgetSession();
      } else {
        if (ranFor >= delays.healthy) delay = delays.first;
        log("session", `restarting in ${delay / 1000} s`);
        await this.restartDelay(delay);
        delay = Math.min(delay * 2, delays.max);
      }
      if (this.stopping) break;
      startedAt = this.now();
      resumed = this.d.store.state.sessionId;
      await this.open(resumed);
      if (closed.length) this.push(MSG.restarted(new Date(this.now()), closed, this.d.cfg.mode));
      this.deliver();
    }
  }

  private restartDelay(ms: number): Promise<void> {
    return new Promise((res) => {
      const t = setTimeout(done, ms);
      const self = this;
      function done() {
        clearTimeout(t);
        self.wakeRestart = null;
        res();
      }
      this.wakeRestart = done;
    });
  }

  forgetSession(): void {
    delete this.d.store.state.sessionId;
    this.d.store.state.usage = {}; // a new session's totals start from zero
    this.d.store.save();
  }

  /** Stops the session for good: open tasks are failed as stopped; the query is closed. */
  async stop(): Promise<string[]> {
    this.stopping = true;
    if (this.watchdog) clearInterval(this.watchdog);
    this.watchdog = null;
    this.wakeRestart?.();
    const closed = this.closeOpen(MSG.stopped, true);
    this.waiting.length = 0;
    return closed;
  }

  closeQuery(): void {
    try {
      this.q?.close();
    } catch {
      /* already gone */
    }
    this.input.detach();
  }

  /** For tests: wait for queued usage rows. */
  async settled(): Promise<void> {
    await this.usageChain;
  }
}

/** A local agent task: an implementer (the only agent the orchestrator has). */
function isImplementerTask(t: any): boolean {
  if (t.task_type) return t.task_type === "local_agent";
  return t.subagent_type === "implementer" || !!taskIdOf(t.description);
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((res, rej) => {
    const t = setTimeout(() => rej(new Error(`no answer in ${ms / 1000} s`)), ms);
    p.then(
      (v) => {
        clearTimeout(t);
        res(v);
      },
      (e) => {
        clearTimeout(t);
        rej(e);
      },
    );
  });
}

function resultError(msg: SDKResultMessage): string {
  if (msg.subtype === "success") return String(msg.result || "error").replace(/\s+/g, " ");
  const errs = (msg as any).errors as string[] | undefined;
  return (errs?.length ? errs.join("; ") : msg.subtype).replace(/\s+/g, " ");
}
