"""vfg_engine.mc.rcon against a fake vanilla server (fake_vanilla.py): whole replies, the end marker,
the lock, interrupts, backoff. No Minecraft needed."""

import fcntl
import os
import signal
import threading
import time

import pytest

from fake_vanilla import FakeVanilla, properties
from vfg_engine.mc.rcon import MAX_COMMAND, Rcon, RconError


@pytest.fixture
def lock(tmp_path, monkeypatch):
    path = str(tmp_path / "rcon.lock")
    monkeypatch.setenv("VFG_RCON_LOCK", path)
    return path


@pytest.fixture
def server():
    srv = FakeVanilla(handler=lambda cmd: "echo " + cmd)
    yield srv
    srv.close()


def test_one_command(tmp_path, lock, server):
    r = Rcon(properties(tmp_path, server))
    assert r.command("time query gametime") == "echo time query gametime"
    assert r.command("/say hi") == "echo say hi"          # a leading slash is dropped
    assert r.stats.exchanges == 2 and r.stats.connects == 1


def test_long_reply_is_read_whole(tmp_path, lock):
    big = "".join("%05d," % i for i in range(3000))          # 18000 chars: five packets
    srv = FakeVanilla(handler=lambda cmd: big if cmd == "big" else "small")
    try:
        r = Rcon(properties(tmp_path, srv))
        assert r.command("big") == big
        assert r.command("next") == "small"
        assert r.stats.multi == 1 and r.stats.markers == 1
    finally:
        srv.close()


@pytest.mark.parametrize("text", ["a" * 4096, "a" * 8192, "é" * 4096, "€" * 4095 + "b"])
def test_a_reply_of_exactly_full_packets_ends(tmp_path, lock, text):
    """After a packet of exactly 4096 units the end is unknowable: the marker ends it."""
    srv = FakeVanilla(handler=lambda cmd: text if cmd == "full" else "after")
    try:
        r = Rcon(properties(tmp_path, srv))
        start = time.monotonic()
        assert r.command("full") == text
        assert time.monotonic() - start < 1.0             # not a timeout
        assert r.command("x") == "after"
        assert r.stats.markers == 1
        assert srv.dropped == 0
    finally:
        srv.close()


def test_a_command_too_long_is_refused_before_it_is_sent(tmp_path, lock, server):
    r = Rcon(properties(tmp_path, server))
    with pytest.raises(RconError, match="over the 1446"):
        r.command("say " + "x" * MAX_COMMAND)
    assert server.commands == []
    assert r.command("ok") == "echo ok"


def test_the_lock_is_held_from_send_to_reply(tmp_path, lock):
    seen = {}

    def handler(cmd):
        fd = os.open(lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen["free"] = True
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BlockingIOError:
            seen["free"] = False
        finally:
            os.close(fd)
        return "done"

    srv = FakeVanilla(handler=handler)
    try:
        r = Rcon(properties(tmp_path, srv))
        assert r.command("x") == "done"
        assert seen["free"] is False                   # another client would have waited
        fd = os.open(lock, os.O_RDONLY)                 # and it is let go after the reply
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)
    finally:
        srv.close()


def test_a_batch_holds_the_lock_and_reenters(tmp_path, lock, server):
    r = Rcon(properties(tmp_path, server))
    with r.held():
        assert r.command("a") == "echo a"
        fd = os.open(lock, os.O_RDONLY)
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)
        assert r.command("b") == "echo b"


class Cut(BaseException):
    pass


def test_a_cut_waits_for_the_reply_and_never_leaks(tmp_path, lock):
    """The engine's 50 ms cut raises from a SIGALRM handler mid-reply. It is held off until the
    reply is in, then lands: the slow command's reply is never read as the next one's."""
    def handler(cmd):
        if cmd == "slow":
            time.sleep(0.05)
            return "SLOW REPLY"
        return "reply to " + cmd

    srv = FakeVanilla(handler=handler)
    old = signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(Cut()))
    try:
        r = Rcon(properties(tmp_path, srv))
        r.command("warm")
        signal.setitimer(signal.ITIMER_REAL, 0.01)
        start = time.monotonic()
        with pytest.raises(Cut):
            r.command("slow")
        assert time.monotonic() - start >= 0.045           # it waited for the reply
        signal.setitimer(signal.ITIMER_REAL, 0)
        assert r.command("next") == "reply to next"
        assert r.stats.connects == 1 and r.stats.broken == 0
        assert srv.commands == ["warm", "slow", "next"]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
        srv.close()


@pytest.mark.parametrize("at", [1, 2, 3, 6, 11])
def test_anything_else_raised_mid_reply_is_recovered(tmp_path, lock, monkeypatch, at):
    """Something raised inside an exchange that is not a signal (here: on the `at`-th packet read,
    of a reply of five packets, its marker and the next command's): the exchange is finished on the
    same socket, and the next reply is the next command's."""
    big = "x" * 4096 * 4 + "tail"
    srv = FakeVanilla(handler=lambda cmd: big if cmd == "big" else "reply to " + cmd)
    try:
        r = Rcon(properties(tmp_path, srv))
        r.command("warm")
        real, count = Rcon._read_packet, {"n": 0}

        def flaky(self, s):
            count["n"] += 1
            if count["n"] == at:
                raise Cut()
            return real(self, s)

        monkeypatch.setattr(Rcon, "_read_packet", flaky)
        try:
            r.command("big")
        except Cut:
            pass
        monkeypatch.setattr(Rcon, "_read_packet", real)
        start = time.monotonic()
        assert r.command("next") == "reply to next"
        assert time.monotonic() - start < 1.0
        assert srv.dropped == 0
    finally:
        srv.close()


def test_server_down_backs_off_in_words(tmp_path, lock):
    srv = FakeVanilla()
    props = properties(tmp_path, srv)
    srv.close()
    time.sleep(0.05)
    r = Rcon(props)
    with pytest.raises(RconError, match="connect to 127.0.0.1"):
        r.command("x")
    with pytest.raises(RconError, match="next try in"):
        r.command("x")


def test_a_wrong_password_is_said(tmp_path, lock):
    srv = FakeVanilla(password="right")
    try:
        p = tmp_path / "server.properties"
        p.write_text("rcon.port=%d\nrcon.password=wrong\n" % srv.port)
        with pytest.raises(RconError, match="refused the password"):
            Rcon(str(p)).command("x")
    finally:
        srv.close()


def test_threads_share_one_connection_without_mixing(tmp_path, lock, server):
    r = Rcon(properties(tmp_path, server))
    bad = []

    def worker(k):
        for i in range(200):
            cmd = "t%d-%d" % (k, i)
            if r.command(cmd) != "echo " + cmd:
                bad.append(cmd)

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert bad == []
    assert r.stats.connects == 1


def test_a_dropped_connection_reconnects(tmp_path, lock, server):
    r = Rcon(properties(tmp_path, server))
    r.command("a")
    r._sock.close()                     # the server (or the network) dropped it
    with pytest.raises(RconError):
        r.command("b")
    assert r.command("c") == "echo c"
    assert r.stats.connects == 2
