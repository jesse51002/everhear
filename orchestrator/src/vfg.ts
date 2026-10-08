// Runs the `vfg` toolkit with an argv array (no shell), a timeout, its exit code and output.

export interface VfgResult {
  code: number;
  stdout: string;
  stderr: string;
  timedOut: boolean;
}

export type VfgRunner = (args: string[], timeoutMs?: number) => Promise<VfgResult>;

export const vfg: VfgRunner = async (args, timeoutMs = 60_000) => {
  let proc: ReturnType<typeof Bun.spawn>;
  try {
    proc = Bun.spawn(["vfg", ...args], {
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
      env: process.env,
    });
  } catch (e) {
    return { code: 127, stdout: "", stderr: String((e as Error).message ?? e), timedOut: false };
  }
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    proc.kill("SIGKILL");
  }, timeoutMs);
  try {
    const [stdout, stderr, code] = await Promise.all([
      new Response(proc.stdout as ReadableStream).text(),
      new Response(proc.stderr as ReadableStream).text(),
      proc.exited,
    ]);
    return { code: timedOut ? 124 : code, stdout, stderr, timedOut };
  } finally {
    clearTimeout(timer);
  }
};

/** First line of stderr (or stdout), for a log line. */
export function why(r: VfgResult): string {
  const line = (r.stderr.trim() || r.stdout.trim()).split("\n")[0] ?? "";
  return r.timedOut ? "timed out" : `exit ${r.code}${line ? ": " + line.slice(0, 200) : ""}`;
}
