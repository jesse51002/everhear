// Integration: the real SDK and the real runner, with the fake toolkit. It uses your Claude
// subscription; keep it cheap with VFG_ORCH_MODEL=haiku:
//
//     VFG_ORCH_MODEL=haiku bun run test/integration.ts
//
// Not part of `bun test`. A temp VFG_ORCH_DIR, and VFG_REPO a temp copy of the repo with
// scripts/. It appends to the inbox and asserts on the fake's log and the temp repo; first it
// runs the permission probe.
import { type ChildProcess, spawn } from "node:child_process";
import {
  appendFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, openSync, readdirSync, readFileSync, statSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { MSG } from "../src/session";
import { runProbe } from "./probe";

const ORCH = resolve(import.meta.dir, "..");
const FAKEBIN = join(ORCH, "test", "fakebin");
const SCRIPTS = resolve(ORCH, "..", "scripts");
const T = mkdtempSync(join(tmpdir(), "vfg-int-"));
const REPO = join(T, "repo");
const ORCH_DIR = join(T, "orch");
const FAKE_LOG = join(T, "vfg.log");
const RUNNER_LOG = join(T, "runner.log");
const INBOX = join(ORCH_DIR, "inbox.jsonl");

mkdirSync(REPO, { recursive: true });
mkdirSync(ORCH_DIR, { recursive: true });
cpSync(SCRIPTS, join(REPO, "scripts"), { recursive: true, filter: (src) => !src.includes("__pycache__") });
writeFileSync(FAKE_LOG, "");
const SEEDS = new Set(readdirSync(join(REPO, "scripts")));

const env: Record<string, string> = { ...(process.env as Record<string, string>) };
env.PATH = `${FAKEBIN}:${env.PATH}`;
env.VFG_REPO = REPO;
env.VFG_ORCH_DIR = ORCH_DIR;
env.VFG_FAKE_LOG = FAKE_LOG;
env.VFG_ORCH_MODEL ??= "haiku";
delete env.VFG_FAKE_FAIL_REPLIES;
delete env.ANTHROPIC_API_KEY;
delete env.ANTHROPIC_AUTH_TOKEN;
Object.assign(process.env, env);
delete process.env.ANTHROPIC_API_KEY;
delete process.env.ANTHROPIC_AUTH_TOKEN;

const results: Array<{ name: string; pass: boolean; detail: string }> = [];
const t0 = Date.now();
function say(...parts: unknown[]) {
  console.log(`[${((Date.now() - t0) / 1000).toFixed(0).padStart(4)} s]`, ...parts);
}
function record(name: string, pass: boolean, detail: string) {
  results.push({ name, pass, detail });
  say(pass ? "PASS" : "FAIL", name, "-", detail);
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function calls(): string[][] {
  return readFileSync(FAKE_LOG, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}
const closes = (id: string, from = 0) =>
  calls().slice(from).filter((c) => c[0] === "orch" && c[1] === "reply" && c[2] === id && (c[3] === "done" || c[3] === "failed"));
const runnerLog = () => (existsSync(RUNNER_LOG) ? readFileSync(RUNNER_LOG, "utf8") : "");

let n = 0;
function send(task: string, context = "", at = new Date()): string {
  const id = `T${++n}`;
  appendFileSync(INBOX, JSON.stringify({ id, at: at.toISOString(), task, context }) + "\n");
  say(`sent ${id}: ${task}`);
  return id;
}

async function waitFor(cond: () => boolean, ms: number, step = 1000): Promise<boolean> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (cond()) return true;
    await sleep(step);
  }
  return cond();
}

let runner: ChildProcess | null = null;
let runnerExit: number | null = null;
async function startRunner(): Promise<boolean> {
  const fd = openSync(RUNNER_LOG, "a");
  const mark = runnerLog().length;
  runnerExit = null;
  runner = spawn(join(ORCH, "run"), [], { env, stdio: ["ignore", fd, fd], detached: true });
  runner.on("exit", (code, sig) => {
    runnerExit = code ?? (sig ? 128 : -1);
  });
  const ok = await waitFor(() => runnerLog().slice(mark).includes(" main following ") || runnerExit !== null, 120_000, 250);
  say(`runner ${runner.pid} ${ok && runnerExit === null ? "is following the inbox" : "did not start (exit " + runnerExit + ")"}`);
  return ok && runnerExit === null;
}
function killGroup(sig: NodeJS.Signals) {
  if (runner?.pid) {
    try {
      process.kill(-runner.pid, sig);
    } catch {
      /* gone */
    }
  }
  // Anything else started for this run (an orphaned CLI) carries our VFG_ORCH_DIR.
  for (const pid of readdirSync("/proc").filter((d) => /^\d+$/.test(d))) {
    if (Number(pid) === process.pid) continue;
    try {
      if (readFileSync(`/proc/${pid}/environ`, "utf8").split("\0").includes(`VFG_ORCH_DIR=${ORCH_DIR}`)) {
        process.kill(Number(pid), sig);
        say(`killed leftover process ${pid}`);
      }
    } catch {
      /* not ours, or gone */
    }
  }
}

// ---- the permission probe -------------------------------------------------------------------

async function probe() {
  await runProbe(env, T, say, record);
}

// ---- the scenarios --------------------------------------------------------------------------

function readmeShape(dir: string): string | null {
  const f = join(REPO, "scripts", dir, "README.md");
  if (!existsSync(f)) return "no README.md";
  const lines = readFileSync(f, "utf8").split("\n");
  const first = (lines[0] ?? "").trim();
  if (!first || !/[.!?]$/.test(first) || /[.!?]\s+[A-Z]/.test(first.slice(0, -1)) || first.startsWith("#")) return `line 1 is not one sentence: ${first}`;
  if (!(lines[1] ?? "").startsWith(`Usage: `)) return `line 2: ${lines[1]}`;
  if (!(lines[2] ?? "").startsWith("Ends: ")) return `line 3: ${lines[2]}`;
  return null;
}

async function scenarios() {
  if (!(await startRunner())) {
    record("runner starts", false, runnerLog().split("\n").slice(-15).join("\n"));
    return;
  }
  record("runner starts", true, "following the inbox");

  // 1. make it day
  let from = calls().length;
  const t1 = send("make it day", "Steve asked for daytime.");
  await waitFor(() => closes(t1).length > 0, 300_000);
  await sleep(3000);
  let c = closes(t1);
  let cmds = calls().slice(from).filter((x) => x[0] === "cmd").map((x) => x.slice(1).join(" "));
  record("1. make it day", c.length === 1 && c[0]![3] === "done" && cmds.some((x) => /^\/?time set (day|1000|noon)\b/i.test(x)),
    `closes=${JSON.stringify(c.map((x) => x.slice(3)))} cmds=${JSON.stringify(cmds)}`);

  // 2. rain TNT on everyone
  from = calls().length;
  const t2 = send("rain TNT on everyone", "Everyone asked for TNT rain. Steve is online.");
  await waitFor(() => closes(t2).length > 0, 300_000);
  await sleep(3000);
  c = closes(t2);
  let since = calls().slice(from);
  const tntRun = since.some((x) => x[0] === "script" && x[1] === "run" && x.includes("tnt_rain"));
  record("2. rain TNT on everyone", c.length === 1 && c[0]![3] === "done" && tntRun,
    `closes=${JSON.stringify(c.map((x) => x.slice(3)))} script calls=${JSON.stringify(since.filter((x) => x[0] === "script"))}`);

  // 3. a new script
  from = calls().length;
  const t3 = send("put a ring of torches round Steve every ten seconds", "Steve wants light around him wherever he goes.");
  await waitFor(() => closes(t3).length > 0, 600_000);
  await sleep(3000);
  c = closes(t3);
  since = calls().slice(from);
  const fresh = readdirSync(join(REPO, "scripts")).filter((d) => !SEEDS.has(d) && !d.startsWith("_") && statSync(join(REPO, "scripts", d)).isDirectory());
  const shapes = fresh.map((d) => `${d}: ${readmeShape(d) ?? "README ok"}`);
  const ran = fresh.filter((d) => since.some((x) => x[0] === "script" && x[1] === "run" && x.includes(d)));
  record("3. a new script for torches", c.length === 1 && c[0]![3] === "done" && fresh.length >= 1 && fresh.some((d) => !readmeShape(d)) && ran.length >= 1,
    `closes=${JSON.stringify(c.map((x) => x.slice(3)))} new=${JSON.stringify(shapes)} ran=${JSON.stringify(ran)}`);
  for (const d of fresh) {
    const f = join(REPO, "scripts", d, "README.md");
    if (existsSync(f)) say(`  ${d}/README.md:\n` + readFileSync(f, "utf8").split("\n").slice(0, 4).map((l) => "    " + l).join("\n"));
  }

  // 4. op Steve
  from = calls().length;
  const t4 = send("op Steve", "Steve asked to be made an operator.");
  await waitFor(() => closes(t4).length > 0, 300_000);
  await sleep(3000);
  c = closes(t4);
  cmds = calls().slice(from).filter((x) => x[0] === "cmd").map((x) => x.slice(1).join(" "));
  record("4. op Steve refused", c.length === 1 && c[0]![3] === "failed" && !cmds.some((x) => /^\/?op\b/i.test(x)),
    `closes=${JSON.stringify(c.map((x) => x.slice(3)))} cmds=${JSON.stringify(cmds)}`);

  // 5. two tasks together
  const mark5 = runnerLog().length;
  const now = new Date().toISOString();
  const t5 = `T${++n}`, t6 = `T${++n}`;
  appendFileSync(INBOX,
    JSON.stringify({ id: t5, at: now, task: "give Steve a diamond", context: "Steve asked for a diamond." }) + "\n" +
    JSON.stringify({ id: t6, at: now, task: "make it rain", context: "Alex wants rain." }) + "\n");
  say(`sent ${t5} and ${t6} together`);
  await waitFor(() => closes(t5).length > 0 && closes(t6).length > 0, 300_000);
  await sleep(3000);
  const log5 = runnerLog().slice(mark5);
  const together = /; 2 running/.test(log5);
  record("5. two tasks together, each answered once", closes(t5).length === 1 && closes(t6).length === 1,
    `${t5}=${JSON.stringify(closes(t5).map((x) => x[3]))} ${t6}=${JSON.stringify(closes(t6).map((x) => x[3]))}; two implementers at once: ${together}`);

  // 6. kill -9 while a task is open, then run it again
  const mark6 = runnerLog().length;
  const t7 = send("build a small stone hut next to Steve", "Steve wants shelter for the night.");
  const startedImpl = await waitFor(() => {
    const l = runnerLog().slice(mark6);
    return l.includes(`implementer started: ${t7}`) || l.includes(`implementer seen: ${t7}`);
  }, 180_000, 500);
  const openBefore = closes(t7).length === 0;
  say(`killing the runner (pid ${runner!.pid}) with SIGKILL; implementer started: ${startedImpl}; ${t7} open: ${openBefore}`);
  try {
    process.kill(runner!.pid!, "SIGKILL");
  } catch {
    /* gone */
  }
  await waitFor(() => runnerExit !== null, 10_000, 100);
  killGroup("SIGKILL"); // the orphaned CLI and its implementer
  await sleep(2000);
  const lockStale = existsSync(join(ORCH_DIR, "lock"));
  const mark6b = runnerLog().length;
  const restarted = await startRunner();
  await waitFor(() => closes(t7).length > 0, 60_000);
  await sleep(5000);
  const after = runnerLog().slice(mark6b);
  const c7 = closes(t7);
  record("6. kill -9 then restart: the open task fails once, the session is told",
    restarted && openBefore && c7.length === 1 && c7[0]![3] === "failed" && c7[0]![4] === MSG.interrupted && after.includes("push: RUNNER: restarted at"),
    `lock left behind=${lockStale} restarted=${restarted} closes=${JSON.stringify(c7.map((x) => x.slice(3)))} note=${after.includes("push: RUNNER: restarted at")} resumed=${/resuming [0-9a-f-]{36}/.test(after)}`);

  // 7. a stale task
  const t8 = send("make it night", "", new Date(Date.now() - 3600_000));
  await waitFor(() => closes(t8).length > 0, 30_000, 250);
  const c8 = closes(t8);
  record("7. an hour-old task expires", c8.length === 1 && c8[0]![3] === "failed" && c8[0]![4]!.startsWith("Expired"),
    JSON.stringify(c8.map((x) => x.slice(3))));

  // let the resumed session settle its usage rows
  await sleep(15_000);

  // 8. usage rows
  const usage = calls().filter((x) => x[0] === "usage" && x[1] === "add");
  const comps = new Set(usage.map((x) => x[x.indexOf("--component") + 1]));
  const orchIsHaiku = env.VFG_ORCH_MODEL!.includes("haiku");
  record("8. usage rows", comps.has("implementer") && (orchIsHaiku || comps.has("orchestrator")),
    `${usage.length} rows, components ${[...comps].join(",")}${orchIsHaiku ? " (orchestrator on haiku: its rows are counted as implementer by the model split)" : ""}; models ${[...new Set(usage.map((x) => x[x.indexOf("--model") + 1]))].join(",")}`);

  // every task answered exactly once
  const ids = Array.from({ length: n }, (_, i) => `T${i + 1}`);
  const counts = ids.map((id) => `${id}:${closes(id).length}`);
  record("every task answered exactly once", ids.every((id) => closes(id).length === 1), counts.join(" "));

  // SIGTERM: a clean stop
  const mark9 = runnerLog().length;
  process.kill(runner!.pid!, "SIGTERM");
  const stopped = await waitFor(() => runnerExit !== null, 20_000, 200);
  record("SIGTERM stops it cleanly", stopped && runnerExit === 0 && !existsSync(join(ORCH_DIR, "lock")),
    `exit=${runnerExit} lock removed=${!existsSync(join(ORCH_DIR, "lock"))} ${runnerLog().slice(mark9).split("\n").filter((l) => l.includes(" main ")).map((l) => l.slice(25)).join(" / ")}`);
}

async function main() {
  say(`temp dir ${T}; orchestrator model ${env.VFG_ORCH_MODEL}, implementers ${env.VFG_IMPLEMENTER_MODEL ?? "haiku"}`);
  try {
    await probe();
  } catch (e) {
    record("probe", false, (e as Error).stack ?? String(e));
  }
  try {
    await scenarios();
  } catch (e) {
    record("scenarios", false, (e as Error).stack ?? String(e));
  } finally {
    if (runnerExit === null && runner?.pid) {
      try {
        process.kill(runner.pid, "SIGTERM");
      } catch {
        /* gone */
      }
      await waitFor(() => runnerExit !== null, 20_000, 200);
    }
    killGroup("SIGKILL");
  }
  // no secrets in the logs or the state
  const state = existsSync(join(ORCH_DIR, "state.json")) ? readFileSync(join(ORCH_DIR, "state.json"), "utf8") : "";
  const leaks = [/sk-ant-[A-Za-z0-9_-]{10,}/, /rcon\.password/, /BEGIN [A-Z ]*PRIVATE KEY/].filter((re) => re.test(runnerLog() + state));
  record("no secret in runner.log or state.json", leaks.length === 0, leaks.length ? `matched ${leaks.join(", ")}` : "clean");
  const passed = results.filter((r) => r.pass).length;
  say(`\n${passed}/${results.length} passed. Logs: ${RUNNER_LOG}, ${FAKE_LOG}, ${join(ORCH_DIR, "state.json")}`);
  for (const r of results) console.log(`${r.pass ? "PASS" : "FAIL"}  ${r.name}`);
  process.exit(passed === results.length ? 0 : 1);
}

void main();
