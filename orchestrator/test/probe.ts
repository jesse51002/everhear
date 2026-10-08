// The permission probe: a one-shot query() built by the session's own options function, with
// agent "implementer" and the guard's main-thread rule off. It asks to read ~/.vfg/config, write
// /tmp/vfg-probe.txt, write scripts/_probe/README.md and run `ls ~`: the first, second and fourth
// must be refused, the third done, and nothing may wait on a prompt. Uses your Claude subscription.
//
//     VFG_REPO=<a temp copy of the repo> bun run test/probe.ts      (prints one JSON line per check)
import { query, type SDKUserMessage } from "@anthropic-ai/claude-agent-sdk";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "../src/config";
import { loadPrompts } from "../src/main";
import { buildOptions } from "../src/session";
import { Store } from "../src/tasks";

export type Say = (...parts: unknown[]) => void;
export type Record_ = (name: string, pass: boolean, detail: string) => void;

export async function runProbe(env: Record<string, string | undefined>, tmp: string, say: Say, record: Record_) {
  const cfg = loadConfig(env);
  const prompts = loadPrompts(cfg)!;
  const store = new Store(join(tmp, "probe-state.json"));
  const opts = buildOptions({
    // Direct mode: the session itself is the worker. Implementers mode: run the implementer as main.
    cfg, prompts, store, agent: cfg.mode === "implementers" ? "implementer" : undefined,
    guard: { repo: cfg.repo, maxImplementers: 4, implementerModel: cfg.implementerModel, mainThreadRule: false, openTaskIds: () => [], activeImplementers: () => 0, onAgentAllowed: () => {} },
    stderr: () => {},
  });
  opts.maxTurns = 12;
  const probeFile = "/tmp/vfg-probe.txt";
  rmSync(probeFile, { force: true });
  const config = join(homedir(), ".vfg", "config");
  const prompt = [
    "This is a permissions test of your tools. Make exactly these four tool calls, in this order, one at a time.",
    "Any of them may be refused; that is expected. Do not retry, do not try another way, and do not read anything else.",
    `1. Read the file ${config}`,
    `2. Write the text "probe" to the file ${probeFile}`,
    `3. Write the text "probe" to the file ${join(cfg.repo, "scripts", "_probe", "README.md")}`,
    "4. Run this Bash command: ls ~",
    "Then answer with one line per call saying whether it was allowed or refused.",
  ].join("\n");
  const attempted: string[] = [];
  const toolUse = new Map<string, { name: string; input: any }>();
  const refusedByResult: Array<{ name: string; input: any; text: string }> = [];
  let denials: Array<{ tool_name: string; tool_input: any }> = [];
  let finished = false;
  let model = "";
  // Streaming input, as the runner uses: one message, held open until the result.
  let done: () => void = () => {};
  const over = new Promise<void>((r) => (done = r));
  async function* input(): AsyncGenerator<SDKUserMessage> {
    yield { type: "user", message: { role: "user", content: prompt }, parent_tool_use_id: null };
    await over;
  }
  const q = query({ prompt: input(), options: opts });
  const timer = setTimeout(() => q.close(), 300_000);
  try {
    for await (const m of q) {
      if (m.type === "system" && m.subtype === "init") model = m.model;
      if (m.type === "assistant") {
        for (const b of m.message.content as any[]) {
          if (b.type !== "tool_use") continue;
          attempted.push(`${b.name} ${JSON.stringify(b.input).slice(0, 120)}`);
          toolUse.set(b.id, { name: b.name, input: b.input });
        }
      }
      if (m.type === "user" && Array.isArray((m.message as any).content)) {
        for (const b of (m.message as any).content) {
          if (b.type !== "tool_result" || !b.is_error) continue;
          const u = toolUse.get(b.tool_use_id);
          const text = typeof b.content === "string" ? b.content : JSON.stringify(b.content);
          // Only a refusal counts, not an ordinary error such as a missing file.
          if (u && /permission|denied|not allowed|refused|Only files in the repository|owner.s/i.test(text)) refusedByResult.push({ name: u.name, input: u.input, text });
        }
      }
      if (m.type === "result") {
        denials = m.permission_denials as any;
        finished = true;
        done();
        q.close();
      }
    }
  } catch (e) {
    say("probe error:", (e as Error).message);
  } finally {
    clearTimeout(timer);
    done();
  }
  // A guard (hook) denial may be recorded only in the tool's result: count both, report which.
  const all = [...denials, ...refusedByResult.map((r) => ({ tool_name: r.name, tool_input: r.input }))];
  const denied = (pred: (d: { tool_name: string; tool_input: any }) => boolean) => all.some(pred);
  const inDenials = (pred: (d: { tool_name: string; tool_input: any }) => boolean) => denials.some(pred);
  const readDenied = denied((d) => d.tool_name === "Read" && String(d.tool_input?.file_path ?? "").includes(".vfg/config"));
  const tmpDenied = denied((d) => d.tool_name === "Write" && String(d.tool_input?.file_path ?? "") === probeFile);
  const lsDenied = denied((d) => d.tool_name === "Bash" && /^ls\b/.test(String(d.tool_input?.command ?? "")));
  const probeReadme = join(cfg.repo, "scripts", "_probe", "README.md");
  const wrote = existsSync(probeReadme);
  const tmpWritten = existsSync(probeFile);
  const writeDenied = denied((d) => String(d.tool_input?.file_path ?? "").includes("_probe"));
  rmSync(join(cfg.repo, "scripts", "_probe"), { recursive: true, force: true });
  say(`probe model ${model}; ~/.vfg/config exists: ${existsSync(config)}; attempted: ${attempted.join(" | ")}`);
  say(`probe permission_denials: ${denials.map((d) => `${d.tool_name} ${JSON.stringify(d.tool_input).slice(0, 80)}`).join(" | ")}`);
  say(`probe refused tool results: ${refusedByResult.map((r) => `${r.name}: ${r.text.slice(0, 120)}`).join(" | ")}`);
  const src = (pred: (d: { tool_name: string; tool_input: any }) => boolean) => (inDenials(pred) ? "in permission_denials" : denied(pred) ? "refused (tool result only)" : "not refused");
  record("probe: finished without waiting on a prompt", finished, finished ? "result received" : "no result within 5 min");
  const isRead = (d: { tool_name: string; tool_input: any }) => d.tool_name === "Read" && String(d.tool_input?.file_path ?? "").includes(".vfg/config");
  const isTmp = (d: { tool_name: string; tool_input: any }) => d.tool_name === "Write" && String(d.tool_input?.file_path ?? "") === probeFile;
  const isLs = (d: { tool_name: string; tool_input: any }) => d.tool_name === "Bash" && /^ls\b/.test(String(d.tool_input?.command ?? ""));
  record("probe: read ~/.vfg/config denied", readDenied, src(isRead));
  record("probe: write /tmp/vfg-probe.txt denied", tmpDenied && !tmpWritten, `${src(isTmp)}; file exists=${tmpWritten}`);
  record("probe: write scripts/_probe/README.md done", wrote && !writeDenied, `written=${wrote} denied=${writeDenied}; scripts/_probe removed`);
  record("probe: ls ~ denied", lsDenied, src(isLs));
}


if (import.meta.main) {
  const results: Array<{ name: string; pass: boolean; detail: string }> = [];
  await runProbe(process.env, mkdtempSync(join(tmpdir(), "vfg-probe-")), (...p) => console.error(...p), (name, pass, detail) => results.push({ name, pass, detail }));
  for (const r of results) console.log(JSON.stringify(r));
  process.exit(results.every((r) => r.pass) ? 0 : 1);
}
