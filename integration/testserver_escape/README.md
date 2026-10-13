# testserver_escape — the sandbox-escape regression test

`test.py` is a deliberately misbehaving `vfg test` (Prototype_Implementer_Quality). It reaches for
the live Minecraft server from inside a test every way the box run of 2026-10-13 found open — the
live RCON password through a live process's `/proc` (`cwd`, `root`, `fd`, `environ`), the live
folders by their own paths, the live RCON/game/bridge ports, a wrong-password RCON login, the
internet, the live RCON lock in `/tmp`, and `sudo` — and prints one `ESCAPE  <attempt>: REACHED …`
or `… refused, …` line each (never a secret value, no live write, no RCON command past a login
with a knowingly wrong password).

`integration/testserver_e2e.py` owns the run: it replaces the literal `__CFG__` with a JSON object
`{"mc", "vfg", "rcon_port", "game_port", "api_port", "lock"}` (the live folders, ports and RCON
lock), writes the file to `made/<server>/tests/<task>.py`, runs `vfg test <task>`, and judges the
`ESCAPE` lines: INFO on the PC (user mode, same user as the live services — weaker, so some reach),
every one must be **refused** under `--box` (the box's system sandbox: a separate test user,
`ProtectProc=invisible`, loopback-only, a private `/tmp`). It proves the test ran with one
`t.check`, so the run exits 0 whenever the test itself worked.
