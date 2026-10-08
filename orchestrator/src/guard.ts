// The PreToolUse hook on every tool. It denies with a reason the model can act on, and
// otherwise returns {} so the allow rules (permissionMode "dontAsk") decide.
import type { HookCallback, HookJSONOutput } from "@anthropic-ai/claude-agent-sdk";
import { realpathSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, resolve } from "node:path";
import { log } from "./config";
import { REPLY_TOOL } from "./reply";

/** Server commands that are the owner's to run. scripts/_lib/mc.py refuses the same words. */
export const REFUSED = new Set(["stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip", "whitelist", "kick", "save-off"]);

/**
 * Server-wide settings an implementer may not change: the orchestrator decides those itself, when
 * a player asked for it (scripts/_lib/mc.py does not refuse them: seeded games use the world border).
 */
export const SERVER_WIDE = new Set(["setworldspawn", "defaultgamemode", "difficulty", "worldborder"]);

export const MAIN_THREAD_TOOLS = new Set(["Agent", "TaskStop", "Read", "Glob", "Grep", REPLY_TOOL]);
/** Hybrid: the orchestrator's tools plus Bash (under the implementer's rules); never Write or Edit. */
export const HYBRID_TOOLS = new Set([...MAIN_THREAD_TOOLS, "Bash"]);

export const DENY = {
  mainThread: "You orchestrate: give this to an implementer.",
  owner: "That is the server owner's to do; report it.",
  serverWide: (word: string) =>
    `${word} changes the whole server: implementers may not run it. Leave it out and say in your report that the orchestrator should do it if a player asked.`,
  lib: "Never edit scripts/_lib/: it is shared by every script. Copy what you need into your own script.",
  expansion:
    "Write the server command literally inside single quotes: no $( ), backticks or variables in a vfg cmd.",
  agentType: 'Use subagent_type "implementer": it is the only agent you have.',
  foreground:
    "Implementers run in the background: leave run_in_background out, end your turn, and you are woken when it reports.",
  overrides: "Leave model, mode and isolation out: the implementer definition sets them.",
  shape:
    "Refused, but Bash works: only these run, each as a plain command (join several with && or ;): " +
    "vfg cmd '<command>' | vfg script deploy|run|stop|logs|list ... | vfg history ... | " +
    "python3 -m py_compile <file> | sleep N. No cd, echo, ls, cat, grep, sed, ps, find, $?, pipes into " +
    "other programs, or redirections: vfg's output and exit status come back to you as they are, and the " +
    "Read, Glob and Grep tools look at files. Send the allowed command again on its own.",
  follow: "vfg script logs --follow never ends: use vfg script logs <name> -n 40, and sleep between looks.",
  outside: "Only the repository can be searched: give a path inside it, or none.",
  readOutside: "Only files in the repository can be read.",
  noAgents: "There are no implementers: do the task yourself with Bash, Read, Write, Edit, Glob and Grep.",
  hybridWrite: "You do not write files: scripts are written by implementers. Start one with the Agent tool.",
  hybridOther: "Do simple tasks with vfg commands in Bash; give everything else to an implementer.",
  background: "Run commands in the foreground: scripts already run detached on the server (vfg script run).",
};

export interface GuardContext {
  repo: string;
  maxImplementers: number;
  implementerModel: string;
  /**
   * True in implementers mode: the main thread only orchestrates. False in direct mode (and the
   * permission probe): the main thread does the work, held to the implementer's rules.
   */
  mainThreadRule: boolean;
  /** Hybrid mode: the main thread orchestrates and also runs simple commands, under the implementer's Bash rules. */
  hybrid?: boolean;
  openTaskIds(): string[];
  /** Implementers running plus those allowed and not yet started. */
  activeImplementers(): number;
  /** An Agent call was allowed: count it until its task_started arrives. */
  onAgentAllowed(taskId: string, toolUseId: string): void;
}

export interface ToolCall {
  tool_name: string;
  tool_input: any;
  agent_id?: string;
  tool_use_id?: string;
}

/** The task id at the head of an implementer's description ("T7 castle at spawn"). */
export function taskIdOf(description: unknown): string | null {
  const m = /^\s*(T\d+)\b/.exec(String(description ?? ""));
  return m ? m[1]! : null;
}

/** The word of `set` a server command runs (its first word, or the word after any `run`), or null. */
export function commandWordIn(cmd: string, set: Set<string>): string | null {
  const words = cmd.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const checks = [words[0]!];
  for (let i = 0; i < words.length - 1; i++) if (words[i]!.toLowerCase() === "run") checks.push(words[i + 1]!);
  for (const w of checks) {
    const word = w.replace(/^\/+/, "").toLowerCase();
    if (set.has(word)) return word;
  }
  return null;
}

/** The refused word in a server command (its first word, or the word after any `run`), or null. */
export function refusedWord(cmd: string): string | null {
  return commandWordIn(cmd, REFUSED);
}

export interface ShellCommand {
  words: string[];
  /** A $( ), backtick or $variable outside single quotes. */
  expands: boolean;
  /** A < or > outside quotes. */
  redirects: boolean;
}

/** Splits a shell command line into simple commands and their words, honouring quotes. */
export function splitShell(line: string): ShellCommand[] {
  const out: ShellCommand[] = [];
  let words: string[] = [];
  let word = "";
  let inWord = false;
  let expands = false;
  let redirects = false;
  const endWord = () => {
    if (inWord) words.push(word);
    word = "";
    inWord = false;
  };
  const endCmd = () => {
    endWord();
    if (words.length || redirects) out.push({ words, expands, redirects });
    words = [];
    expands = false;
    redirects = false;
  };
  for (let i = 0; i < line.length; i++) {
    const c = line[i]!;
    if (c === "'") {
      inWord = true;
      const j = line.indexOf("'", i + 1);
      const end = j < 0 ? line.length : j;
      word += line.slice(i + 1, end);
      i = end;
    } else if (c === '"') {
      inWord = true;
      let j = i + 1;
      for (; j < line.length && line[j] !== '"'; j++) {
        const d = line[j]!;
        if (d === "\\" && j + 1 < line.length && '"\\$`\n'.includes(line[j + 1]!)) {
          word += line[++j];
        } else {
          if (d === "$" || d === "`") expands = true;
          word += d;
        }
      }
      i = j;
    } else if (c === "\\") {
      inWord = true;
      if (i + 1 < line.length) word += line[++i];
    } else if (c === " " || c === "\t") {
      endWord();
    } else if (c === "\n" || c === ";" || c === "&" || c === "|" || c === "(" || c === ")") {
      endCmd();
    } else {
      if (c === "$" || c === "`") expands = true;
      if (c === "<" || c === ">") redirects = true;
      inWord = true;
      word += c;
    }
  }
  endCmd();
  return out;
}

/** One of the commands the allow rules let an implementer run (session.ts `rules`). */
export function allowedForm(words: string[]): boolean {
  const [a, b, c] = words;
  if (a === "sleep") return words.length >= 2;
  if (a === "python3") return b === "-m" && c === "py_compile" && words.length >= 4;
  if (a !== "vfg") return false;
  if (b === "cmd") return words.length >= 3;
  if (b === "history") return true;
  if (b !== "script") return false;
  if (c === "list") return words.length === 3;
  if (c === "deploy") return words.length <= 4;
  return (c === "run" || c === "stop" || c === "logs") && words.length >= 4;
}

/**
 * Why an implementer's Bash command is refused, or null to let the rules decide. Claude Code's
 * own refusal of a disallowed command reads as if Bash itself were off, and a small model stops
 * trying; this one says which commands do run.
 */
export function checkBash(command: string, implementer = false): string | null {
  const cmds = splitShell(command);
  // Server commands that are the owner's, however they are dressed up; and, for an implementer, the
  // server-wide settings that are the orchestrator's.
  for (const cmd of cmds) {
    const w = cmd.words.slice();
    while (w.length && /^[A-Za-z_][A-Za-z0-9_]*=/.test(w[0]!)) w.shift(); // FOO=bar vfg ...
    const prog = w[0]?.split("/").pop();
    if (prog !== "vfg" || w[1] !== "cmd") continue;
    const args = w.slice(2);
    for (const a of [args.join(" "), ...args]) if (refusedWord(a)) return DENY.owner;
    if (implementer) {
      for (const a of [args.join(" "), ...args]) {
        const word = commandWordIn(a, SERVER_WIDE);
        if (word) return DENY.serverWide(word);
      }
    }
  }
  for (const cmd of cmds) {
    const w = cmd.words;
    if (w[0] === "vfg" && w[1] === "cmd" && cmd.expands) return DENY.expansion;
    if (cmd.redirects || cmd.expands || !allowedForm(w)) return DENY.shape;
    if (w[0] === "vfg" && w[1] === "script" && w[2] === "logs" && w.includes("--follow")) return DENY.follow;
  }
  return null;
}

/** The guard's decision for one tool call: a reason to deny it, or null to let the rules decide. */
export function decide(call: ToolCall, g: GuardContext): string | null {
  const name = call.tool_name;
  const input = call.tool_input ?? {};
  const mainThread = !call.agent_id;
  const hybrid = mainThread && !!g.hybrid && g.mainThreadRule;
  if (hybrid && !HYBRID_TOOLS.has(name)) return name === "Write" || name === "Edit" ? DENY.hybridWrite : DENY.hybridOther;
  if (mainThread && g.mainThreadRule && !hybrid && !MAIN_THREAD_TOOLS.has(name)) return DENY.mainThread;
  // Bash and file rules as for an implementer: implementers themselves, the direct-mode session (and
  // the permission probe, with the main-thread rule off), and the hybrid session's own commands.
  const implementer = !mainThread || !g.mainThreadRule || hybrid;

  if (name === "Agent" || name === "Task") {
    if (implementer && !hybrid) return DENY.noAgents; // direct mode (or an implementer): nobody to brief
    if (input.subagent_type !== "implementer") return DENY.agentType;
    const id = taskIdOf(input.description);
    const open = g.openTaskIds();
    if (!id || !open.includes(id)) {
      return open.length
        ? `Start the description with the id of an open task, like "${open[0]} castle at spawn". Open now: ${open.join(", ")}.`
        : "No task is open, so there is nothing to start an implementer for.";
    }
    if (input.run_in_background === false) return DENY.foreground;
    if ((input.mode && input.mode !== "dontAsk") || input.isolation || (input.model && input.model !== g.implementerModel)) {
      return DENY.overrides;
    }
    if (g.activeImplementers() >= g.maxImplementers) {
      return `${g.maxImplementers} implementers are running; wait for a report.`;
    }
    return null;
  }

  if (name === "Bash" && implementer) {
    if (input.run_in_background === true) return DENY.background;
    // A real implementer (a subagent) may not change server-wide settings; the session itself may.
    return checkBash(String(input.command ?? ""), !!call.agent_id);
  }

  // The Glob and Grep allow rules are unrestricted: keep them, and Read, inside the repository.
  if ((name === "Glob" || name === "Grep") && input.path && !within(g.repo, String(input.path))) return DENY.outside;
  if (name === "Read" && input.file_path && !within(g.repo, String(input.file_path))) return DENY.readOutside;

  if ((name === "Write" || name === "Edit") && input.file_path) {
    if (within(resolve(g.repo, "scripts/_lib"), String(input.file_path), g.repo)) return DENY.lib;
  }
  return null;
}

export function makeGuard(g: GuardContext): HookCallback {
  const seenEffort = new Set<string>();
  return async (input, toolUseID): Promise<HookJSONOutput> => {
    if (input.hook_event_name !== "PreToolUse") return {};
    // The effort a turn actually runs at, after any downgrade for its model: logged once per agent.
    const level = input.effort?.level;
    const who = input.agent_id ? `implementer ${input.agent_id.slice(0, 8)}` : "orchestrator";
    if (level && !seenEffort.has(`${who} ${level}`)) {
      seenEffort.add(`${who} ${level}`);
      log("effort", `${who} runs at ${level}`);
    }
    const call: ToolCall = {
      tool_name: input.tool_name,
      tool_input: input.tool_input,
      agent_id: input.agent_id,
      tool_use_id: input.tool_use_id ?? toolUseID,
    };
    const reason = decide(call, g);
    if (reason) {
      const who = call.agent_id ? `implementer ${call.agent_id.slice(0, 8)}` : "orchestrator";
      log("guard", `denied ${who} ${call.tool_name}: ${reason} (${brief(call.tool_input)})`);
      return {
        hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: reason },
      };
    }
    if (call.tool_name === "Agent" || call.tool_name === "Task") {
      g.onAgentAllowed(taskIdOf(call.tool_input?.description)!, call.tool_use_id ?? "");
    }
    return {};
  };
}

/** The real path, following symlinks as far as the path exists (/home -> /var/home). */
export function realish(p: string): string {
  try {
    return realpathSync(p);
  } catch {
    const parent = dirname(p);
    return parent === p ? p : resolve(realish(parent), basename(p));
  }
}

/** Is `p` (relative to `base`, default `dir`) inside `dir`? */
export function within(dir: string, p: string, base = dir): boolean {
  const root = realish(dir);
  const real = realish(resolve(base, expandHome(p)));
  return real === root || real.startsWith(root + "/");
}

function expandHome(p: string): string {
  return p === "~" ? homedir() : p.startsWith("~/") ? homedir() + p.slice(1) : p;
}

export function brief(input: unknown): string {
  const s = JSON.stringify(input ?? {});
  return s.length > 200 ? s.slice(0, 200) + "…" : s;
}
