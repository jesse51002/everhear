"""The runner's start-up exits, run as a real process. HOME is a temp dir in every case, so
even a runner that got past its checks could not reach a Claude login."""
import contextlib
import os
import re
import shutil
import subprocess
import sys
import time

from helpers import FAKEBIN, ORCH, config_in, read, temp_dir, write

from orchestrator.main import is_runner, load_prompts, release_lock, take_lock


def run(cwd: str, env: dict) -> tuple[int, str]:
    p = subprocess.run([sys.executable, "-m", "orchestrator"], cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    return p.returncode, p.stdout.decode() + p.stderr.decode()


def env(d: str, path: str) -> dict:
    return {"PATH": path, "HOME": d, "VFG_ORCH_DIR": os.path.join(d, "orch"), "VFG_REPO": d,
            "VFG_FAKE_LOG": os.path.join(d, "vfg.log")}


@contextlib.contextmanager
def runner_stand_in():
    """A live process whose command line is a runner's (`… -m orchestrator`), that is not one."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "-m", "orchestrator"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.monotonic() + 3
        while time.monotonic() < end:
            try:
                with open("/proc/%d/cmdline" % other.pid, "rb") as f:
                    if b"time.sleep" in f.read():
                        break
            except OSError:
                pass  # not there yet
            time.sleep(0.01)
        yield other.pid
    finally:
        other.kill()
        other.wait()


def test_vfg_not_on_the_path_exit_2():
    d = temp_dir()
    code, out = run(ORCH, {**env(d, "/usr/bin:/bin"), "VFG_ORCH_MODE": "hybrid"})
    assert code == 2
    assert "fatal: vfg is not on the PATH" in out
    assert re.search(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z main ", out, re.M)
    # Not in the TypeScript test: a mode that is gone is named in a warning, and ignored.
    assert "main warning: VFG_ORCH_MODE=hybrid is ignored: every task goes to an implementer" in out


def test_a_prompt_missing_exit_2():
    d = temp_dir()
    copy = os.path.join(d, "orchestrator")
    os.mkdir(copy)
    shutil.copytree(os.path.join(ORCH, "orchestrator"), os.path.join(copy, "orchestrator"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.mkdir(os.path.join(copy, "prompts"))
    write(os.path.join(copy, "prompts", "orchestrator.md"), "x")
    code, out = run(copy, env(d, "%s:/usr/bin:/bin" % FAKEBIN))
    assert code == 2
    assert "a prompt file is missing" in out


def test_another_live_runner_holds_the_lock_exit_1():
    d = temp_dir()
    os.mkdir(os.path.join(d, "orch"))
    with runner_stand_in() as pid:
        write(os.path.join(d, "orch", "lock"), "%d\n" % pid)
        code, out = run(ORCH, env(d, "%s:/usr/bin:/bin" % FAKEBIN))
        assert code == 1
        assert "already running" in out
        assert read(os.path.join(d, "orch", "lock")).strip() == str(pid)


def test_the_prompts_load():
    d = temp_dir()
    prompts = load_prompts(config_in(d))
    assert "%MAX_IMPLEMENTERS%" in prompts.orchestrator
    assert "%REPO%" in prompts.orchestrator
    assert "reply(task, status, text)" in prompts.orchestrator
    assert "RESULT: done | failed" in prompts.implementer
    assert load_prompts(config_in(d, {"VFG_ORCH_MODE": "direct"})) == prompts  # the mode is ignored
    assert sorted(os.listdir(os.path.join(ORCH, "prompts"))) == ["implementer.md", "orchestrator.md"]
    # saved places, in both
    assert "SAVED PLACES" in prompts.orchestrator and "vfg place list" in prompts.orchestrator
    assert "vfg place save <name> --at <x> <y> <z> --by <task id>" in prompts.implementer
    assert "PLACE: " in prompts.implementer


def test_a_dead_pids_lock_is_taken_over_and_released():
    d = temp_dir()
    lock = os.path.join(d, "lock")
    write(lock, "999999999\n")
    assert take_lock(lock) is True
    assert read(lock).strip() == str(os.getpid())
    release_lock(lock)
    assert not os.path.exists(lock)


def test_a_live_lock_is_not_taken():
    d = temp_dir()
    lock = os.path.join(d, "lock")
    with runner_stand_in() as pid:
        write(lock, "%d\n" % pid)
        assert take_lock(lock) is False


# Not in the TypeScript tests: the lock's liveness check knows the Python runner and the old bun
# one, and a live pid that is neither does not hold the lock.
def test_a_runner_is_known_by_its_command_line_python_or_the_old_bun_one():
    assert is_runner(b"/repo/orchestrator/.venv/bin/python\0-m\0orchestrator\0")
    assert is_runner(b"/home/k/.bun/bin/bun\0run\0src/main.ts\0")
    assert not is_runner(b"/usr/bin/python3\0-m\0http.server\0")
    assert not is_runner(b"sleep\0" + b"30\0")
    d = temp_dir()
    lock = os.path.join(d, "lock")
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        write(lock, "%d\n" % sleeper.pid)
        assert take_lock(lock) is True  # alive, but not a runner
        release_lock(lock)
    finally:
        sleeper.kill()
        sleeper.wait()
