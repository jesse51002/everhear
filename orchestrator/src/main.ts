// The runner: config, single-instance lock, signals, and the loops.
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, unlinkSync, writeSync } from "node:fs";
import { join } from "node:path";
import { type Config, loadConfig, log } from "./config";
import { InboxFollower } from "./inbox";
import { Outbox } from "./reply";
import { FatalError, MSG, type Prompts, Session } from "./session";
import { Store } from "./tasks";
import { UsageMeter } from "./usage";
import { vfg } from "./vfg";

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "EPERM") return false;
  }
  try {
    // A pid reused by something that is not a runner does not hold the lock.
    const cmd = readFileSync(`/proc/${pid}/cmdline`, "utf8");
    return cmd.includes("bun") || cmd.includes("main.ts");
  } catch {
    return true;
  }
}

/** Creates `lock` holding this pid. False when another live runner holds it; a dead pid is taken over. */
export function takeLock(file: string): boolean {
  for (let i = 0; i < 3; i++) {
    try {
      const fd = openSync(file, "wx");
      writeSync(fd, `${process.pid}\n`);
      closeSync(fd);
      return true;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "EEXIST") throw e;
      let pid = 0;
      try {
        pid = Number(readFileSync(file, "utf8").trim());
      } catch {
        /* vanished meanwhile */
      }
      if (pid && pid !== process.pid && alive(pid)) return false;
      try {
        unlinkSync(file);
      } catch {
        /* someone else took it over first */
      }
    }
  }
  return false;
}

export function releaseLock(file: string): void {
  try {
    if (Number(readFileSync(file, "utf8").trim()) === process.pid) unlinkSync(file);
  } catch {
    /* already gone */
  }
}

/**
 * The session's prompt, and the implementer's: hybrid.md and implementer.md (hybrid),
 * orchestrator.md and implementer.md (implementers), direct.md alone (direct).
 */
export function loadPrompts(cfg: Config): Prompts | null {
  if (cfg.mode === "direct") {
    const d = join(cfg.promptsDir, "direct.md");
    return existsSync(d) ? { orchestrator: readFileSync(d, "utf8"), implementer: "" } : null;
  }
  const o = join(cfg.promptsDir, cfg.mode === "hybrid" ? "hybrid.md" : "orchestrator.md");
  const i = join(cfg.promptsDir, "implementer.md");
  if (!existsSync(o) || !existsSync(i)) return null;
  return { orchestrator: readFileSync(o, "utf8"), implementer: readFileSync(i, "utf8") };
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  log("main", `starting: pid ${process.pid}, ${cfg.mode} mode, ${cfg.model} at ${cfg.effort} effort${cfg.mode !== "direct" ? `, implementers ${cfg.implementerModel} at ${cfg.implementerEffort} effort` : ""}, repo ${cfg.repo}, state ${cfg.orchDir}`);

  // 2. Fatal: no vfg, or a prompt missing.
  if (!Bun.which("vfg")) {
    log("main", "fatal: vfg is not on the PATH");
    process.exit(2);
  }
  const prompts = loadPrompts(cfg);
  if (!prompts) {
    log("main", `fatal: a prompt file is missing in ${cfg.promptsDir}`);
    process.exit(2);
  }

  // 3. One runner at a time.
  mkdirSync(cfg.orchDir, { recursive: true });
  if (!takeLock(cfg.lockFile)) {
    log("main", `already running (pid ${readFileSync(cfg.lockFile, "utf8").trim()})`);
    process.exit(1);
  }

  // 4. State; tasks closed over a day ago go.
  const store = new Store(cfg.stateFile);
  store.load();
  const dropped = store.dropClosedBefore(24);
  if (dropped.length) log("main", `dropped ${dropped.length} tasks closed over a day ago`);

  const outbox = new Outbox(store, vfg);
  store.onReply = () => outbox.kick();
  const usage = new UsageMeter(store, vfg, cfg.mode, cfg.implementerModel);
  let follower: InboxFollower | null = null;
  let session: Session;

  // 5. A new conversation when asked for.
  if (existsSync(cfg.freshFile)) {
    unlinkSync(cfg.freshFile);
    delete store.state.sessionId;
    store.state.usage = {};
    log("main", "fresh: starting a new session");
  }

  // 6. Interrupted tasks are failed, never re-run.
  const interrupted = store.openIds();
  for (const id of interrupted) store.fail(id, MSG.interrupted);
  if (interrupted.length) log("main", `closed as interrupted: ${interrupted.join(", ")}`);
  store.save();

  let stopping = false;
  const shutdown = async (code: number, reason: string) => {
    if (stopping) return;
    stopping = true;
    log("main", `stopping: ${reason}`);
    follower?.stop();
    const closed = session ? await session.stop() : [];
    if (closed.length) log("main", `closed as stopped: ${closed.join(", ")}`);
    outbox.start(); // replies recorded before the loops started go out too
    const sent = await outbox.drain(10_000);
    if (!sent) log("main", `${outbox.pending()} replies unsent; they go out on the next start`);
    store.save();
    session?.closeQuery();
    await outbox.stop();
    releaseLock(cfg.lockFile);
    log("main", `stopped (exit ${code})`);
    process.exit(code);
  };
  const fatal = (e: Error) => {
    log("main", `fatal: ${e.message}`);
    void shutdown(2, "fatal");
  };
  process.on("SIGTERM", () => void shutdown(0, "SIGTERM"));
  process.on("SIGINT", () => void shutdown(0, "SIGINT"));
  process.on("unhandledRejection", (e) => log("main", `unhandled rejection: ${(e as Error)?.stack ?? e}`));
  process.on("uncaughtException", (e) => log("main", `uncaught exception: ${e?.stack ?? e}`));

  session = new Session({ cfg, store, outbox, usage, prompts, onFatal: fatal });

  // 7. The session, resuming the saved one; an API key is fatal.
  const startedAt = Date.now();
  try {
    await session.open(store.state.sessionId);
  } catch (e) {
    if (e instanceof FatalError) {
      log("main", `fatal: ${e.message}`);
      session.closeQuery();
      store.save();
      releaseLock(cfg.lockFile);
      process.exit(2);
    }
    throw e;
  }
  if (stopping) return;

  // 8. Tell the session what the restart closed.
  if (interrupted.length) session.push(MSG.restarted(new Date(), interrupted, cfg.mode));

  // 9. The inbox follower, the outbox and the watchdog.
  follower = new InboxFollower({
    inbox: cfg.inbox,
    store,
    staleMinutes: cfg.staleMinutes,
    onTask: (id, message) => {
      usage.note([id]);
      session.pushTask(id, message);
    },
  });
  follower.start(250);
  outbox.start();
  session.startWatchdog(10_000);
  log("main", `following ${cfg.inbox}`);
  session.keepAlive(startedAt).catch((e) => {
    if (e instanceof FatalError) fatal(e);
    else log("main", `the session keeper broke: ${(e as Error).stack ?? e}`);
  });
}

if (import.meta.main) {
  main().catch((e) => {
    log("main", `fatal: ${(e as Error).stack ?? e}`);
    process.exit(2);
  });
}
