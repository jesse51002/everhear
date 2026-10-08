// Shared bits for the unit tests: temp dirs, the fake vfg on the PATH, a quiet log.
import { mkdtempSync, readFileSync, existsSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { setQuiet, loadConfig, type Config } from "../src/config";
import { Store } from "../src/tasks";

setQuiet(true);

export const FAKEBIN = resolve(import.meta.dir, "fakebin");
export const ORCH = resolve(import.meta.dir, "..");

export function tempDir(prefix = "vfg-orch-test-"): string {
  return mkdtempSync(join(tmpdir(), prefix));
}

/** Puts the fake vfg first on the PATH and logs its calls to a fresh file. */
export function useFakeVfg(dir: string): { log: string; failFile: string; calls: () => string[][] } {
  const log = join(dir, "vfg.log");
  const failFile = join(dir, "fail-replies");
  writeFileSync(log, "");
  process.env.PATH = `${FAKEBIN}:${(process.env.PATH ?? "").split(":").filter((p) => p !== FAKEBIN).join(":")}`;
  process.env.VFG_FAKE_LOG = log;
  process.env.VFG_FAKE_FAIL_REPLIES = failFile;
  return {
    log,
    failFile,
    calls: () =>
      existsSync(log)
        ? readFileSync(log, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as string[])
        : [],
  };
}

export function testConfig(dir: string, extra: Record<string, string> = {}): Config {
  return loadConfig({ VFG_ORCH_DIR: join(dir, "orch"), VFG_REPO: dir, ...extra });
}

export function newStore(dir: string): Store {
  const s = new Store(join(dir, "state.json"));
  s.load();
  return s;
}

export async function until(cond: () => boolean, ms = 3000, step = 10): Promise<void> {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error("timed out waiting");
    await new Promise((r) => setTimeout(r, step));
  }
}
