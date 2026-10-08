// The runner's start-up exits, run as a real process. HOME is a temp dir in every case, so
// even a runner that got past its checks could not reach a Claude login.
import { describe, expect, test } from "bun:test";
import { cpSync, existsSync, mkdirSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { loadPrompts, releaseLock, takeLock } from "../src/main";
import { FAKEBIN, ORCH, tempDir, testConfig } from "./helpers";

const BUN_DIR = dirname(process.execPath);

function run(main: string, env: Record<string, string>) {
  const p = Bun.spawnSync([process.execPath, "run", main], { env, stdout: "pipe", stderr: "pipe", timeout: 20_000 });
  return { code: p.exitCode, out: p.stdout.toString() + p.stderr.toString() };
}

function env(dir: string, path: string): Record<string, string> {
  return { PATH: path, HOME: dir, VFG_ORCH_DIR: join(dir, "orch"), VFG_REPO: dir, VFG_FAKE_LOG: join(dir, "vfg.log") };
}

describe("runner start-up", () => {
  test("vfg not on the PATH: exit 2", () => {
    const dir = tempDir();
    const r = run(join(ORCH, "src/main.ts"), env(dir, `${BUN_DIR}:/usr/bin:/bin`));
    expect(r.code).toBe(2);
    expect(r.out).toContain("fatal: vfg is not on the PATH");
    expect(r.out).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z main /m);
  });

  test("a prompt missing: exit 2", () => {
    const dir = tempDir();
    const copy = join(dir, "orchestrator");
    mkdirSync(copy);
    cpSync(join(ORCH, "src"), join(copy, "src"), { recursive: true });
    cpSync(join(ORCH, "package.json"), join(copy, "package.json"));
    symlinkSync(join(ORCH, "node_modules"), join(copy, "node_modules"));
    mkdirSync(join(copy, "prompts"));
    writeFileSync(join(copy, "prompts", "orchestrator.md"), "x");
    const r = run(join(copy, "src/main.ts"), env(dir, `${FAKEBIN}:${BUN_DIR}:/usr/bin:/bin`));
    expect(r.code).toBe(2);
    expect(r.out).toContain("a prompt file is missing");
  });

  test("another live runner holds the lock: exit 1", () => {
    const dir = tempDir();
    mkdirSync(join(dir, "orch"));
    writeFileSync(join(dir, "orch", "lock"), `${process.pid}\n`); // this bun process is alive
    const r = run(join(ORCH, "src/main.ts"), env(dir, `${FAKEBIN}:${BUN_DIR}:/usr/bin:/bin`));
    expect(r.code).toBe(1);
    expect(r.out).toContain("already running");
    expect(readFileSync(join(dir, "orch", "lock"), "utf8").trim()).toBe(String(process.pid));
  });

  test("the prompts load in both modes", () => {
    const dir = tempDir();
    const direct = loadPrompts(testConfig(dir, { VFG_ORCH_MODE: "direct" }));
    expect(direct!.orchestrator).toContain("%REPO%");
    expect(direct!.orchestrator).toContain("reply(task, status, text)");
    const hybrid = loadPrompts(testConfig(dir));
    expect(hybrid!.orchestrator).toContain("DO IT YOURSELF, OR START AN IMPLEMENTER");
    expect(hybrid!.implementer).toContain("RESULT: done | failed");
    const impl = loadPrompts(testConfig(dir, { VFG_ORCH_MODE: "implementers" }));
    expect(impl!.orchestrator).toContain("%MAX_IMPLEMENTERS%");
    expect(impl!.implementer).toContain("RESULT: done | failed");
  });

  test("a dead pid's lock is taken over, and released", () => {
    const dir = tempDir();
    const lock = join(dir, "lock");
    writeFileSync(lock, "999999999\n");
    expect(takeLock(lock)).toBe(true);
    expect(readFileSync(lock, "utf8").trim()).toBe(String(process.pid));
    releaseLock(lock);
    expect(existsSync(lock)).toBe(false);
  });

  test("a live lock is not taken", () => {
    const dir = tempDir();
    const lock = join(dir, "lock");
    const other = Bun.spawn([process.execPath, "-e", "await Bun.sleep(5000)"], { stdout: "ignore", stderr: "ignore" });
    const end = Date.now() + 3000;
    while (Date.now() < end) {
      try {
        if (readFileSync(`/proc/${other.pid}/cmdline`, "utf8").includes("Bun.sleep")) break;
      } catch {
        /* not there yet */
      }
      Bun.sleepSync(10);
    }
    writeFileSync(lock, `${other.pid}\n`);
    try {
      expect(takeLock(lock)).toBe(false);
    } finally {
      other.kill();
    }
  });
});
