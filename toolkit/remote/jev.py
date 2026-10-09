# jev.py: one question to Jev from the box, for any caller on the PC (vfg route first). ARGS:
#   {"vfg": "/opt/vfg", "state": {...}, "questions": {name: question, ...}, "timeout": 3}
# Prints one JSON object:
#   {"answers": {name: answer as Jev sent it}, "input_tokens": N or null,
#    "estimated_tokens": N, "ms": wall time, "calls": [{"questions", "ms", "input_tokens"}]}
#
# The key is TYPESAFE_API_KEY in <vfg>/.env (Contracts §11), read here and never
# printed; the endpoint is VFG_JEV_URL from the same file, else TypeSafe's. The
# request is the Jev filter's (jev/jev_api.py): POST {"model", "state",
# "questions"} with a bearer key, one attempt, answers keyed like the questions.
#
# A request holds at most 64k tokens, 32k of them the state plus its longest
# question (measured live on 2026-10-09: a 31.3k state answered, 33.3k was a 400;
# 65.4k in all answered, about 72k was a 400). No limit on how many questions:
# 860 in one request answered in 0.76 s. Questions that do not fit one request
# go in several, each with the whole state, sent at once; nothing is ever cut.
#
# Sizes are estimated from the UTF-8 bytes of the JSON. A token was 2.6 to 6
# bytes live: 3.4 for the router's questions, 2.6 for short questions heavy in
# JSON, 6 for plain repeated words. So requests are split at the Jev filter's
# estimate (bytes / 3) against 56k, room for the worst seen; and a state and
# question are refused before sending only when even 6 bytes a token is over 32k.
# Between the two, the request is sent, and a 400 says the size may be why.
import math
import threading
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_NAME = "TYPESAFE_API_KEY"
REQUEST_MAX = 64000           # tokens in one request
REQUEST_SPLIT = 56000         # what a request is split at, by the estimate (see above)
STATE_AND_QUESTION_MAX = 32000
MOST_BYTES_PER_TOKEN = 6.0    # the most seen live: what is over the limit even at this, is
OVERHEAD = 128                # the body's own keys, the model's name, Jev's own, in tokens
REFUSED_STATUSES = (400, 413, 422)


def estimate(obj, per=3.0):
    return int(math.ceil(len(json.dumps(obj, ensure_ascii=False).encode("utf-8")) / per))


def read_env(path):
    """KEY=value lines, as systemd's EnvironmentFile reads them; {} when unreadable."""
    found = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                name, eq, value = line.partition("=")
                if not eq or name.strip().startswith("#"):
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                found[name.strip()] = value.strip()
    except OSError:
        pass
    return found


def reason(body, key):
    """A short, safe reason from an error body: the API's own message, never the key."""
    text = body.decode("utf-8", errors="replace").strip()
    try:
        got = json.loads(text)
    except ValueError:
        got = None
    if isinstance(got, dict):
        for name in ("error", "message", "detail"):
            v = got.get(name)
            if isinstance(v, dict):
                v = v.get("message") or v.get("msg") or v.get("type")
            if isinstance(v, str) and v:
                text = v
                break
        else:
            text = "(no message)"
    text = " ".join(text.split())[:200]
    if key:
        text = text.replace(key, "***")
    return text or "(empty body)"


def batches(state, questions):
    """The questions in order, split so each request with the whole state fits."""
    room = REQUEST_SPLIT - estimate(state) - OVERHEAD
    out, cur, used = [], {}, 0
    for name, q in questions.items():
        cost = estimate({name: q})
        if cur and used + cost > room:
            out.append(cur)
            cur, used = {}, 0
        cur[name] = q
        used += cost
    if cur:
        out.append(cur)
    return out


def call(url, key, model, state, questions, timeout, slot):
    """One request; fills `slot` with its answers, tokens and ms, or an error line."""
    body = json.dumps({"model": model, "state": state, "questions": questions},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            why = reason(e.read(), key)
        except Exception:
            why = "(unreadable body)"
        word = "refused the request" if e.code in REFUSED_STATUSES else "failed"
        slot["error"] = "jev %s: %d %s" % (word, e.code, why)
        return
    except urllib.error.URLError as e:
        r = e.reason
        if isinstance(r, socket.timeout) or "timed out" in str(r):
            slot["error"] = "jev did not answer within %g s" % timeout
        else:
            slot["error"] = "jev unreachable: %s" % (str(r).replace(key, "***") if key else r)
        return
    except socket.timeout:
        slot["error"] = "jev did not answer within %g s" % timeout
        return
    except Exception as e:
        slot["error"] = "jev failed: %s" % type(e).__name__
        return
    slot["ms"] = int((time.monotonic() - t0) * 1000)
    try:
        got = json.loads(raw.decode("utf-8"))
    except ValueError:
        slot["error"] = "jev answered 200 with a body that is not JSON"
        return
    if not isinstance(got, dict):
        slot["error"] = "jev answered 200 with a body that is not an object"
        return
    answers = got.get("answers") if isinstance(got.get("answers"), dict) else {}
    slot["answers"] = {n: answers[n] for n in questions if isinstance(answers.get(n), dict)}
    usage = got.get("usage")
    tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
    if isinstance(tokens, bool) or not isinstance(tokens, (int, float)):
        tokens = None
    slot["input_tokens"] = None if tokens is None else int(tokens)


def main():
    state, questions = ARGS.get("state"), ARGS.get("questions")
    if not isinstance(state, dict):
        die(2, "the state is a JSON object")
    if not isinstance(questions, dict) or not questions \
            or not all(isinstance(q, dict) for q in questions.values()):
        die(2, "the questions are a JSON object of questions, at least one")
    timeout = float(ARGS.get("timeout") or 3)
    env = read_env(os.path.join(ARGS["vfg"], ".env"))
    key = env.get(KEY_NAME, "")
    if not key:
        die(3, "not configured: no %s in %s" % (KEY_NAME, os.path.join(ARGS["vfg"], ".env")))
    url = env.get("VFG_JEV_URL") or ENDPOINT
    model = ARGS.get("model") or MODEL

    longest = max(questions.values(), key=lambda q: estimate(q))
    least = estimate(state, MOST_BYTES_PER_TOKEN) + estimate(longest, MOST_BYTES_PER_TOKEN)
    size = estimate(state) + estimate(longest)
    if least > STATE_AND_QUESTION_MAX:
        die(1, "the state and its longest question are at least %d tokens; Jev takes at most %d "
               "— nothing was cut and nothing was sent" % (least, STATE_AND_QUESTION_MAX))
    groups = batches(state, questions)
    slots = [{} for _ in groups]
    t0 = time.monotonic()
    threads = [threading.Thread(target=call, args=(url, key, model, state, g, timeout, s), daemon=True)
               for g, s in zip(groups, slots)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 1)
    ms = int((time.monotonic() - t0) * 1000)
    for t, s in zip(threads, slots):
        if t.is_alive() and not s:
            s["error"] = "jev did not answer within %g s" % timeout
    errors = [s["error"] for s in slots if "error" in s]
    if errors and errors[0].startswith("jev refused the request: 400") and size > STATE_AND_QUESTION_MAX:
        errors[0] += " — the state and its longest question may be over its %d tokens (about %d)" % (
            STATE_AND_QUESTION_MAX, size)
    if errors:
        die(1, errors[0] + ("" if len(slots) == 1 else " (%d of %d requests failed)"
                                                       % (len(errors), len(slots))))
    answers, counted = {}, []
    for s in slots:
        answers.update(s["answers"])
        counted.append(s["input_tokens"])
    print(json.dumps({
        "answers": answers,
        "input_tokens": None if None in counted else sum(counted),
        "estimated_tokens": sum(estimate(state) + estimate(g) + OVERHEAD for g in groups),
        "ms": ms,
        "calls": [{"questions": len(g), "ms": s["ms"], "input_tokens": s["input_tokens"]}
                  for g, s in zip(groups, slots)],
    }, ensure_ascii=False))


main()
