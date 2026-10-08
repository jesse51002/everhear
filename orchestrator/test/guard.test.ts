import { describe, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, symlinkSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { checkBash, decide, DENY, type GuardContext, makeGuard, refusedWord, splitShell } from "../src/guard";

const REPO = "/home/k/everhear";

function ctx(over: Partial<GuardContext> = {}): GuardContext & { allowed: string[] } {
  const allowed: string[] = [];
  return {
    repo: REPO,
    maxImplementers: 4,
    implementerModel: "haiku",
    mainThreadRule: true,
    openTaskIds: () => ["T7", "T9"],
    activeImplementers: () => 0,
    onAgentAllowed: (t, u) => allowed.push(`${t}:${u}`),
    allowed,
    ...over,
  };
}

const main = (tool_name: string, tool_input: object = {}) => ({ tool_name, tool_input, tool_use_id: "tu1" });
const impl = (tool_name: string, tool_input: object = {}) => ({ tool_name, tool_input, agent_id: "a1", tool_use_id: "tu2" });
const agent = (input: object) => main("Agent", { subagent_type: "implementer", description: "T7 castle at spawn", prompt: "do it", ...input });
const bash = (command: string) => impl("Bash", { command });

describe("the guard", () => {
  const table: Array<[string, ReturnType<typeof main>, string | null, Partial<GuardContext>?]> = [
    // the main thread orchestrates
    ["main-thread Bash", main("Bash", { command: "vfg cmd 'time set day'" }), DENY.mainThread],
    ["main-thread Write", main("Write", { file_path: `${REPO}/scripts/x/main.py`, content: "" }), DENY.mainThread],
    ["main-thread Edit", main("Edit", { file_path: `${REPO}/scripts/x/main.py` }), DENY.mainThread],
    ["main-thread unknown tool", main("WebFetch", { url: "x" }), DENY.mainThread],
    ["main-thread Read", main("Read", { file_path: `${REPO}/scripts/README.md` }), null],
    ["main-thread Glob", main("Glob", { pattern: "scripts/*/README.md" }), null],
    ["main-thread Grep", main("Grep", { pattern: "tnt", path: `${REPO}/scripts` }), null],
    ["main-thread TaskStop", main("TaskStop", { task_id: "x" }), null],
    ["main-thread reply", main("mcp__orch__reply", { task: "T7", status: "done", text: "ok" }), null],
    // Agent
    ["Agent ok", agent({}), null],
    ["Agent another type", agent({ subagent_type: "general-purpose" }), DENY.agentType],
    ["Agent no type", agent({ subagent_type: undefined }), DENY.agentType],
    ["Agent no task id", agent({ description: "castle at spawn" }), 'Start the description with the id of an open task, like "T7 castle at spawn". Open now: T7, T9.'],
    ["Agent a closed task", agent({ description: "T3 castle" }), 'Start the description with the id of an open task, like "T7 castle at spawn". Open now: T7, T9.'],
    ["Agent nothing open", agent({}), "No task is open, so there is nothing to start an implementer for.", { openTaskIds: () => [] }],
    ["Agent T70 is not T7", agent({ description: "T70 castle" }), 'Start the description with the id of an open task, like "T7 castle at spawn". Open now: T7, T9.'],
    ["Agent in the foreground", agent({ run_in_background: false }), DENY.foreground],
    ["Agent in the background", agent({ run_in_background: true }), null],
    ["Agent over the limit", agent({}), "4 implementers are running; wait for a report.", { activeImplementers: () => 4 }],
    ["Agent under the limit", agent({}), null, { activeImplementers: () => 3 }],
    ["Agent another model", agent({ model: "opus" }), DENY.overrides],
    ["Agent the implementer model", agent({ model: "haiku" }), null],
    ["Agent bypassing permissions", agent({ mode: "bypassPermissions" }), DENY.overrides],
    ["Agent in a worktree", agent({ isolation: "worktree" }), DENY.overrides],
    // implementer Bash: the server commands that are the owner's
    ["vfg cmd 'op Steve'", bash("vfg cmd 'op Steve'"), DENY.owner],
    ["vfg cmd 'execute as @a run stop'", bash("vfg cmd 'execute as @a run stop'"), DENY.owner],
    ["vfg cmd 'time set day'", bash("vfg cmd 'time set day'"), null],
    ["vfg cmd /OP Steve", bash("vfg cmd /OP Steve"), DENY.owner],
    ["vfg cmd \"ban-ip x\"", bash('vfg cmd "ban-ip 1.2.3.4"'), DENY.owner],
    ["vfg cmd whitelist", bash("vfg cmd 'whitelist add Bob'"), DENY.owner],
    ["vfg cmd kick after run", bash("vfg cmd 'execute at Steve run /kick Steve'"), DENY.owner],
    ["vfg cmd save-off", bash("vfg cmd save-off"), DENY.owner],
    ["vfg cmd pardon", bash("vfg cmd 'PARDON Steve'"), DENY.owner],
    ["chained after a good command", bash("vfg script run tnt_rain && vfg cmd 'deop Steve'"), DENY.owner],
    ["after a semicolon", bash("sleep 2; vfg cmd 'stop'"), DENY.owner],
    ["a flag before the command", bash("vfg cmd --quiet 'op Steve'"), DENY.owner],
    ["an env prefix", bash("FOO=1 vfg cmd 'op Steve'"), DENY.owner],
    ["a full path", bash("/usr/local/bin/vfg cmd 'kick Steve'"), DENY.owner],
    ["command substitution", bash('vfg cmd "$(echo op) Steve"'), DENY.expansion],
    ["a variable", bash("vfg cmd $CMD"), DENY.expansion],
    ["a dollar in single quotes is literal", bash("vfg cmd 'say costs $5'"), null],
    ["stop inside a word", bash("vfg cmd 'say stop that'"), null],
    ["run inside JSON text", bash(`vfg cmd 'tellraw @a {"text":"run stop"}'`), null],
    ["script run is not a server command", bash("vfg script run anti_camp 15"), null],
    ["script stop is fine", bash("vfg script stop tnt_rain"), null],
    ["other commands get the shape reason", bash("ls ~"), DENY.shape],
    // shapes implementers tend to send
    ["seen: vfg cmd then echo $?", bash(`vfg cmd 'time set day'; echo "EXIT=$?"`), DENY.shape],
    ["seen: give then echo", bash(`vfg cmd 'give Steve minecraft:diamond 1'; echo "EXIT_CODE=$?"`), DENY.shape],
    ["seen: ps | grep; ls; find", bash(`ps -eo pid,etime,args | grep -i '[t]nt_rain'; echo "ps exit: $?"; ls -la /x/scripts/tnt_rain/ 2>&1; find /tmp -maxdepth 3 \\( -iname '*tnt_rain*' \\) 2>/dev/null | head -20`), DENY.shape],
    ["seen: cd && ls; grep; sed", bash(`cd /x/repo/scripts && ls; ls _lib; grep -n "^def " _lib/mc.py; sed -n '/^def pos/,/^def /p' _lib/mc.py | head -30`), DENY.shape],
    // the shapes that run
    ["sleep then logs", bash("sleep 5 && vfg script logs tnt_rain -n 20"), null],
    ["compile then run", bash("python3 -m py_compile scripts/torch_ring/main.py && vfg script run torch_ring"), null],
    ["run with --wait", bash("vfg script run --wait 30 gun_chests 4"), null],
    ["deploy all", bash("vfg script deploy"), null],
    ["deploy one", bash("vfg script deploy torch_ring"), null],
    ["list", bash("vfg script list"), null],
    ["history", bash("vfg history --source ai --since -10m"), null],
    ["two commands joined by ;", bash("vfg cmd 'time set day'; vfg cmd 'weather clear'"), null],
    ["a redirection", bash("vfg cmd 'time set day' 2>&1"), DENY.shape],
    ["to /dev/null", bash("vfg script stop tnt_rain > /dev/null"), DENY.shape],
    ["a global vfg option", bash("vfg --timeout 30 cmd 'time set day'"), DENY.shape],
    ["vfg say is not for implementers", bash("vfg say 'hi'"), DENY.shape],
    ["logs --follow never ends", bash("vfg script logs tnt_rain --follow"), DENY.follow],
    ["sleep with a variable", bash("sleep $N"), DENY.shape],
    ["a > inside quotes is no redirection", bash(`vfg cmd 'tellraw @a {"text":"a > b"}'`), null],
    // implementer files
    ["Write a new script", impl("Write", { file_path: `${REPO}/scripts/torch_ring/main.py` }), null],
    ["Write _lib", impl("Write", { file_path: `${REPO}/scripts/_lib/mc.py` }), DENY.lib],
    ["Edit _lib", impl("Edit", { file_path: `${REPO}/scripts/_lib/mc.py` }), DENY.lib],
    ["Glob outside the repo", impl("Glob", { pattern: "*", path: "~/.ssh" }), DENY.outside],
    ["Grep outside the repo", impl("Grep", { pattern: ".", path: `${homedir()}/.vfg` }), DENY.outside],
    ["Grep with ..", impl("Grep", { pattern: ".", path: `${REPO}/../..` }), DENY.outside],
    ["Grep inside", impl("Grep", { pattern: "tnt", path: `${REPO}/scripts` }), null],
    ["Glob without a path", impl("Glob", { pattern: "scripts/*/README.md" }), null],
    ["Read inside", impl("Read", { file_path: `${REPO}/scripts/tnt_rain/main.py` }), null],
    ["Read a relative path", impl("Read", { file_path: "scripts/README.md" }), null],
    ["Read ~/.vfg/config", impl("Read", { file_path: "~/.vfg/config" }), DENY.readOutside],
    ["Read ~/.ssh", impl("Read", { file_path: `${homedir()}/.ssh/id_ed25519` }), DENY.readOutside],
    ["Read /etc", main("Read", { file_path: "/etc/passwd" }), DENY.readOutside],
    ["Write _lib by a relative path", impl("Write", { file_path: "scripts/_lib/mc.py" }), DENY.lib],
  ];
  for (const [name, call, want, over] of table) {
    test(name, () => expect(decide(call as any, ctx(over))).toBe(want));
  }

  test("direct mode: the main thread works, held to the implementer's rules", () => {
    const g = ctx({ mainThreadRule: false });
    expect(decide(main("Bash", { command: "vfg cmd 'time set day'" }), g)).toBeNull();
    expect(decide(main("Bash", { command: "vfg script run tnt_rain && sleep 3 && vfg script logs tnt_rain -n 20" }), g)).toBeNull();
    expect(decide(main("Bash", { command: "vfg cmd 'op Steve'" }), g)).toBe(DENY.owner);
    expect(decide(main("Bash", { command: "cat scripts/zoo/README.md" }), g)).toBe(DENY.shape);
    expect(decide(main("Bash", { command: "vfg script run zoo Steve", run_in_background: true }), g)).toBe(DENY.background);
    expect(decide(main("Write", { file_path: `${REPO}/scripts/torch_ring/main.py` }), g)).toBeNull();
    expect(decide(main("Write", { file_path: `${REPO}/scripts/_lib/mc.py` }), g)).toBe(DENY.lib);
    expect(decide(main("Read", { file_path: "~/.vfg/config" }), g)).toBe(DENY.readOutside);
    expect(decide(main("Agent", { subagent_type: "implementer", description: "T7 x", prompt: "p" }), g)).toBe(DENY.noAgents);
    expect(decide(main("mcp__orch__reply", { task: "T7", status: "done", text: "ok" }), g)).toBeNull();
  });

  test("hybrid mode: the session runs guarded commands and briefs implementers, never writes", () => {
    const g = ctx({ mainThreadRule: true, hybrid: true });
    expect(decide(main("Bash", { command: "vfg cmd 'time set day'" }), g)).toBeNull();
    expect(decide(main("Bash", { command: "vfg cmd 'give Steve minecraft:diamond_sword 1'" }), g)).toBeNull();
    expect(decide(main("Bash", { command: "vfg script run tnt_rain && vfg script list" }), g)).toBeNull();
    expect(decide(main("Bash", { command: "vfg cmd 'op Steve'" }), g)).toBe(DENY.owner);
    expect(decide(main("Bash", { command: "vfg cmd 'execute as @a run stop'" }), g)).toBe(DENY.owner);
    expect(decide(main("Bash", { command: "cat scripts/zoo/README.md" }), g)).toBe(DENY.shape);
    expect(decide(main("Bash", { command: "vfg script run zoo Steve", run_in_background: true }), g)).toBe(DENY.background);
    expect(decide(main("Write", { file_path: `${REPO}/scripts/house/main.py`, content: "" }), g)).toBe(DENY.hybridWrite);
    expect(decide(main("Edit", { file_path: `${REPO}/scripts/zoo/main.py` }), g)).toBe(DENY.hybridWrite);
    expect(decide(main("WebFetch", { url: "x" }), g)).toBe(DENY.hybridOther);
    expect(decide(main("Read", { file_path: `${REPO}/scripts/README.md` }), g)).toBeNull();
    expect(decide(main("Read", { file_path: "~/.vfg/config" }), g)).toBe(DENY.readOutside);
    expect(decide(main("Grep", { pattern: "house", path: `${REPO}/scripts` }), g)).toBeNull();
    expect(decide(main("TaskStop", { task_id: "x" }), g)).toBeNull();
    expect(decide(main("mcp__orch__reply", { task: "T7", status: "done", text: "ok" }), g)).toBeNull();
    // Agent: the usual implementer checks
    expect(decide(agent({}), g)).toBeNull();
    expect(decide(agent({ subagent_type: "general-purpose" }), g)).toBe(DENY.agentType);
    expect(decide(agent({ run_in_background: false }), g)).toBe(DENY.foreground);
    expect(decide(agent({}), ctx({ mainThreadRule: true, hybrid: true, activeImplementers: () => 4 }))).toBe("4 implementers are running; wait for a report.");
    // the implementers it starts keep their own rules, Write included
    expect(decide(impl("Write", { file_path: `${REPO}/scripts/house/main.py` }) as any, g)).toBeNull();
    expect(decide(impl("Bash", { command: "vfg cmd 'kick Steve'" }) as any, g)).toBe(DENY.owner);
  });

  test("server-wide settings: refused for implementers, allowed for the session itself", () => {
    const g = ctx({ mainThreadRule: true, hybrid: true });
    for (const [cmd, word] of [
      ["vfg cmd 'setworldspawn'", "setworldspawn"],
      ["vfg cmd 'setworldspawn 0 64 0'", "setworldspawn"],
      ["vfg cmd '/Difficulty peaceful'", "difficulty"],
      ["vfg cmd 'execute as @a run difficulty hard'", "difficulty"],
      ["vfg cmd 'worldborder set 100'", "worldborder"],
      ["vfg cmd 'defaultgamemode creative'", "defaultgamemode"],
      ["vfg cmd 'time set day' && vfg cmd 'worldborder center 0 0'", "worldborder"],
      ["vfg cmd setworldspawn 1 2 3", "setworldspawn"],
    ] as const) {
      expect(decide(impl("Bash", { command: cmd }) as any, g)).toBe(DENY.serverWide(word));
      expect(decide(main("Bash", { command: cmd }), g)).toBeNull(); // hybrid session: a player asked
      expect(decide(main("Bash", { command: cmd }), ctx({ mainThreadRule: false }))).toBeNull(); // direct session
    }
    expect(DENY.serverWide("difficulty")).toContain("orchestrator");
    expect(decide(impl("Bash", { command: "vfg cmd 'say difficulty is hard'" }) as any, g)).toBeNull();
    expect(decide(impl("Bash", { command: "vfg cmd 'time set day'" }) as any, g)).toBeNull();
    expect(decide(impl("Bash", { command: "vfg script run demon_lord" }) as any, g)).toBeNull(); // scripts are not refused
    expect(decide(impl("Bash", { command: "vfg cmd 'op Steve'" }) as any, g)).toBe(DENY.owner);
  });

  test("an implementer's background Bash is refused too", () => {
    expect(decide(impl("Bash", { command: "sleep 30", run_in_background: true }) as any, ctx())).toBe(DENY.background);
  });

  test("the probe: implementer checks on the main thread when its rule is off", () => {
    const g = ctx({ mainThreadRule: false });
    expect(decide(main("Bash", { command: "ls ~" }), g)).toBe(DENY.shape);
    expect(decide(main("Bash", { command: "vfg cmd 'op x'" }), g)).toBe(DENY.owner);
    expect(decide(main("Write", { file_path: `${REPO}/scripts/_probe/README.md` }), g)).toBeNull();
  });

  test("the hook's output shape, and allowed Agent calls are counted", async () => {
    const g = ctx();
    const hook = makeGuard(g);
    const base = { session_id: "s", transcript_path: "t", cwd: REPO, hook_event_name: "PreToolUse" as const };
    const denied = await hook({ ...base, tool_name: "Bash", tool_input: { command: "x" }, tool_use_id: "u1" } as any, "u1", { signal: new AbortController().signal });
    expect(denied).toEqual({
      hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: DENY.mainThread },
    });
    const ok = await hook({ ...base, tool_name: "Agent", tool_input: { subagent_type: "implementer", description: "T9 tnt", prompt: "p" }, tool_use_id: "u2" } as any, "u2", { signal: new AbortController().signal });
    expect(ok).toEqual({});
    expect(g.allowed).toEqual(["T9:u2"]);
  });

  test("a symlinked path to the repo counts as inside", () => {
    const real = mkdtempSync(join(tmpdir(), "vfg-guard-"));
    mkdirSync(join(real, "scripts", "_lib"), { recursive: true });
    const link = real + "-link";
    symlinkSync(real, link);
    const g = ctx({ repo: real });
    expect(decide(impl("Read", { file_path: `${link}/scripts/x.md` }) as any, g)).toBeNull();
    expect(decide(impl("Write", { file_path: `${link}/scripts/_lib/mc.py` }) as any, g)).toBe(DENY.lib);
    expect(decide(impl("Grep", { pattern: "x", path: link }) as any, g)).toBeNull();
  });

  test("helpers", () => {
    expect(refusedWord("/Ban-IP x")).toBe("ban-ip");
    expect(refusedWord("execute if entity @a run pardon x")).toBe("pardon");
    expect(refusedWord("time set day")).toBeNull();
    expect(splitShell(`a 'b c' "d \\"e\\"" f\\ g; h | i && j`).map((c) => c.words)).toEqual([["a", "b c", 'd "e"', "f g"], ["h"], ["i"], ["j"]]);
    expect(checkBash("vfg cmd 'list' && vfg cmd 'data get entity Steve Pos'")).toBeNull();
  });
});
