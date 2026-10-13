// The guard's bridge in OpenCode (Prototype_Implementer_Harness): the one part of the OpenCode
// adapter that is not Python, because OpenCode's plugins can only be JS. It decides nothing.
//
// - Before every tool call (tool.execute.before), it asks the brain's guard, at the address and
//   with the secret the adapter put in OpenCode's environment, and throws the guard's reason to
//   refuse: OpenCode gives the model that throw as the tool's error.
// - When the guard cannot be asked (no address, no answer, a bad answer), the call is refused:
//   it fails closed.
// - A shell a tool starts (shell.env) gets back the environment the adapter changed for OpenCode
//   alone: the XDG folders as they were, and the guard's address, its secret, the server's
//   password and the provider's key blanked.

const GUARD = process.env.VFG_GUARD_URL
const SECRET = process.env.VFG_GUARD_SECRET
const SHELL_ENV = (() => {
  try {
    return JSON.parse(process.env.VFG_SHELL_ENV || "{}")
  } catch {
    return {}
  }
})()
// The guard answers in milliseconds, or in a few seconds when it asks vfg what runs.
const TIMEOUT_MS = 60000

export const VfgGuard = async () => ({
  "tool.execute.before": async (input, output) => {
    if (!GUARD || !SECRET) throw new Error("The guard is not set up, so this call was not run.")
    let res
    try {
      res = await fetch(GUARD, {
        method: "POST",
        headers: { "content-type": "application/json", authorization: "Bearer " + SECRET },
        body: JSON.stringify({ tool: input.tool, session: input.sessionID, call: input.callID, args: output.args }),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      })
    } catch (e) {
      throw new Error("The guard could not be reached, so this call was not run: " + ((e && e.message) || e))
    }
    let answer = null
    try {
      answer = await res.json()
    } catch {}
    if (!res.ok || !answer || typeof answer !== "object")
      throw new Error("The guard did not answer (HTTP " + res.status + "), so this call was not run.")
    if (answer.deny) throw new Error(String(answer.deny))
  },
  "shell.env": async (input, output) => {
    Object.assign(output.env, SHELL_ENV)
  },
})
