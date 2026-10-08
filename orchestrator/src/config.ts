// Environment, paths and the log line format.
import { existsSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

export type Mode = "hybrid" | "implementers" | "direct";

export interface Config {
  /** The session's working directory and the scripts folder's parent, resolved: VFG_REPO, else the repo this orchestrator/ is in. */
  repo: string;
  /** Inbox, log, pid, state, lock, fresh. */
  orchDir: string;
  inbox: string;
  stateFile: string;
  lockFile: string;
  freshFile: string;
  /** orchestrator/prompts, beside src/. */
  promptsDir: string;
  /**
   * "hybrid" (default): simple tasks itself, the rest to implementer subagents. "implementers": every
   * task to an implementer. "direct": every task itself.
   */
  mode: Mode;
  model: string;
  effort: string;
  /** The implementer subagents' own effort (AgentDefinition.effort). */
  implementerEffort: string;
  /** A fresh session once a main-thread call's input passes this many tokens (0: never). */
  maxContext: number;
  implementerModel: string;
  maxImplementers: number;
  staleMinutes: number;
  implementerMinutes: number;
}

function expandHome(p: string): string {
  if (p === "~") return homedir();
  if (p.startsWith("~/")) return join(homedir(), p.slice(2));
  return p;
}

function real(p: string): string {
  const abs = resolve(expandHome(p));
  try {
    return existsSync(abs) ? realpathSync(abs) : abs;
  } catch {
    return abs;
  }
}

function int(v: string | undefined, def: number, min = 1): number {
  const n = v === undefined || v === "" ? NaN : Number(v);
  return Number.isFinite(n) && n >= min ? Math.floor(n) : def;
}

export function loadConfig(env: Record<string, string | undefined> = process.env): Config {
  const orchDir = resolve(expandHome(env.VFG_ORCH_DIR || "~/.vfg/orch"));
  return {
    repo: real(env.VFG_REPO || dirname(dirname(import.meta.dir))),
    orchDir,
    inbox: join(orchDir, "inbox.jsonl"),
    stateFile: join(orchDir, "state.json"),
    lockFile: join(orchDir, "lock"),
    freshFile: join(orchDir, "fresh"),
    promptsDir: join(dirname(import.meta.dir), "prompts"),
    mode: env.VFG_ORCH_MODE === "direct" || env.VFG_ORCH_MODE === "implementers" ? env.VFG_ORCH_MODE : "hybrid",
    model: env.VFG_ORCH_MODEL || "opus",
    effort: env.VFG_ORCH_EFFORT || "low",
    implementerModel: env.VFG_IMPL_MODEL || env.VFG_IMPLEMENTER_MODEL || "sonnet",
    implementerEffort: env.VFG_IMPL_EFFORT || "low",
    maxContext: env.VFG_ORCH_MAX_CONTEXT === "0" ? 0 : int(env.VFG_ORCH_MAX_CONTEXT, 70_000),
    maxImplementers: int(env.VFG_ORCH_MAX_IMPLEMENTERS, 4),
    staleMinutes: int(env.VFG_ORCH_STALE_MINUTES, 10),
    implementerMinutes: int(env.VFG_IMPLEMENTER_MINUTES, 10),
  };
}

let quiet = false;
/** Tests silence the log. */
export function setQuiet(q: boolean): void {
  quiet = q;
}

/** One log line on stdout: `<ISO time> <area> <message>`; `vfg orch start` sends it to runner.log. */
export function log(area: string, ...parts: unknown[]): void {
  if (quiet) return;
  const msg = parts
    .map((p) => (typeof p === "string" ? p : p instanceof Error ? p.message : JSON.stringify(p)))
    .join(" ")
    .replace(/\n/g, " ");
  process.stdout.write(`${new Date().toISOString()} ${area} ${msg}\n`);
}

/** HH:MM:SS of a date, UTC. */
export function hms(d: Date): string {
  return d.toISOString().slice(11, 19);
}
