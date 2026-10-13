"""Slow model calls, for the live tests (integration/brain_live.py): a stand-in for a provider that
takes its time, in the brain's own process. On PYTHONPATH with VFG_TEST_SLOW set, it holds chosen
calls back before their request goes out; with VFG_TEST_SLOW unset it does nothing.

    VFG_TEST_SLOW="sixteen golden carrots|1|50;joke about creepers|0|25|Response"

Each rule is `words|step|seconds[|answer]`, the answer a Step (a brain loop's step, the default) or
a Response (the conversation agent's answer to a line): the first such call whose line holds
`words` (a step's THE EVENT line, the agent's THE LINE; case ignored) and, for a step, whose
trailer says `STEP <step> of`, waits `seconds` before vfg_llm sends it, inside the call's own
timeout (structured()'s asyncio.timeout covers every attempt), as a slow provider's answer would.
Each rule fires once. A router's call, and any other process that cannot import vfg_llm, is left
alone. It logs one line to stdout, which is brain.log in the brain's process: `<time> test slow
call: held Step "<words>" s<step> <seconds> s`.

Why a shim and not a setting: the brain's model address is vfg_llm's constant, and the spec's checks
need a step past 45 s that lands inside its timeout (60 s with no reasoning), a loop still thinking
when the next line comes, and a player's turn busy when a report arrives: a real model seldom takes
that long on cue, and this test must not change the brain to make it.
"""
import os

_RULES = os.environ.get("VFG_TEST_SLOW", "").strip()

if _RULES:
    try:
        import asyncio
        import re
        import sys
        from datetime import datetime, timezone

        import vfg_llm
    except Exception:  # noqa: BLE001 — not the brain's process: nothing to slow
        vfg_llm = None
    if vfg_llm is not None:
        _rules = []
        for part in _RULES.split(";"):
            bits = [b.strip() for b in part.split("|")]
            if len(bits) in (3, 4) and bits[0]:
                _rules.append({"words": bits[0].lower(), "step": int(bits[1]), "s": float(bits[2]),
                               "answer": bits[3] if len(bits) == 4 and bits[3] else "Step", "fired": False})
        # A step's event line, and the conversation agent's line (brain/brain/call.py Event.show,
        # brain/brain/respond.py Respond.tail).
        _LINE = {"Step": re.compile(r"THE EVENT:\nLINE #\d+, [\d:]+: \S+ (?:said|typed): ([^\n]*)"),
                 "Response": re.compile(r"THE LINE #\d+, [\d:]+: \S+ (?:said|typed): ([^\n]*)")}
        _STEP = re.compile(r"STEP (\d+) of at most \d+")
        _real_post = vfg_llm._post

        def _texts(body):
            content = (body.get("messages") or [{}])[0].get("content")
            if isinstance(content, str):
                return [content]
            return [p.get("text") or "" for p in content or [] if isinstance(p, dict)]

        async def _post(http, body, key, timeout, flight):
            answer = ((body.get("response_format") or {}).get("json_schema") or {}).get("name")
            if answer in _LINE:
                texts = _texts(body)
                line = next((m.group(1).lower() for t in texts for m in [_LINE[answer].search(t)] if m), "")
                step = _STEP.search(texts[-1] if texts else "")
                for r in _rules:
                    if r["fired"] or r["answer"] != answer or r["words"] not in line:
                        continue
                    if answer == "Step" and not (step and int(step.group(1)) == r["step"]):
                        continue
                    r["fired"] = True
                    at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                    sys.stdout.write('%s test slow call: held %s "%s" s%d %g s\n' % (at, answer, r["words"], r["step"],
                                                                                    r["s"]))
                    sys.stdout.flush()
                    await asyncio.sleep(r["s"])
                    break
            return await _real_post(http, body, key, timeout, flight)

        vfg_llm._post = _post
