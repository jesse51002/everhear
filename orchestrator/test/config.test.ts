import { describe, expect, test } from "bun:test";
import { realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { loadConfig } from "../src/config";

describe("config", () => {
  test("the repo defaults to the one this orchestrator/ is in", () => {
    const repo = realpathSync(join(dirname(import.meta.dir), ".."));
    expect(loadConfig({}).repo).toBe(repo);
    expect(loadConfig({ VFG_REPO: "" }).repo).toBe(repo);
  });

  test("VFG_REPO wins", () => {
    const dir = realpathSync(import.meta.dir);
    expect(loadConfig({ VFG_REPO: dir }).repo).toBe(dir);
  });
});
