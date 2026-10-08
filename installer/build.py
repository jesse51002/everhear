#!/usr/bin/env python3
"""build.py -- fills the installer templates; publish.sh runs it, and so do the tests.

    build.py installers --out DIR --address A ... < token
        DIR/vfg-install.sh, DIR/vfg-install.ps1 and DIR/vfg-install.cmd, with the address, the
        client token (read from stdin, never from argv) and the download links baked in.
    build.py page --out DIR --address A --sh-url U --cmd-url U --ps1-url U --expires EPOCH
        DIR/index.html, the page players open.

In a template, @@VFG_NAME@@ becomes the value quoted for that language (bash, PowerShell or
HTML), and @@=VFG_NAME@@ the bare value, for comments; a bare value may hold only
[A-Za-z0-9 .:+_-]. Every value must be printable ASCII, which keeps both kinds of quoting exact.
"""
import argparse
import datetime
import html
import pathlib
import re
import shlex
import sys

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
PLACEHOLDER = re.compile(r"@@(=?)(VFG_[A-Z0-9_]+)@@")
BARE = re.compile(r"[A-Za-z0-9 .:+_-]*")
PRINTABLE = re.compile(r"[\x20-\x7e]*")


def die(msg):
    sys.stderr.write("build.py: %s\n" % msg)
    sys.exit(1)


def bash_quote(v):
    return shlex.quote(v)


def ps_quote(v):
    return "'" + v.replace("'", "''") + "'"


def html_quote(v):
    return html.escape(v, quote=True)


def fill(text, values, quote, name):
    def rep(m):
        bare, key = m.group(1), m.group(2)
        if key not in values:
            die("%s: no value for %s" % (name, key))
        v = values[key]
        if bare:
            if not BARE.fullmatch(v):
                die("%s: %s cannot go in bare" % (name, key))
            return v
        return quote(v)

    out = PLACEHOLDER.sub(rep, text)
    if "@@VFG_" in out or "@@=VFG_" in out:
        die("%s: a placeholder was left unfilled" % name)
    return out


def check(values):
    for k, v in values.items():
        if not isinstance(v, str) or not PRINTABLE.fullmatch(v):
            die("%s is not printable ASCII" % k)


def write(path, text, newline="\n"):
    data = text.replace("\r\n", "\n")
    if newline != "\n":
        data = data.replace("\n", newline)
    path.write_bytes(data.encode("ascii"))


def installers(a):
    token = sys.stdin.readline().strip()
    if not token or any(c.isspace() for c in token) or len(token) > 512:
        die("the client token on stdin is empty, has spaces, or is too long")
    for name, value in (("--jar-sha256", a.jar_sha256), ("--profile-sha256", a.profile_sha256)):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            die("%s is not a sha256" % name)
    if not re.fullmatch(r"[0-9a-f]{128}", a.fabric_api_sha512):
        die("--fabric-api-sha512 is not a sha512")
    for name in (a.jar_name, a.fabric_api_name):
        if not re.fullmatch(r"[A-Za-z0-9._+-]+\.jar", name):
            die("%s is not a plain jar file name" % name)
    values = {
        "VFG_SERVER_ADDRESS": a.address,
        "VFG_CLIENT_TOKEN": token,
        "VFG_MC_VERSION": a.mc_version,
        "VFG_LOADER_VERSION": a.loader_version,
        "VFG_JAR_NAME": a.jar_name,
        "VFG_JAR_URL": a.jar_url,
        "VFG_JAR_SHA256": a.jar_sha256,
        "VFG_FABRIC_API_NAME": a.fabric_api_name,
        "VFG_FABRIC_API_URL": a.fabric_api_url,
        "VFG_FABRIC_API_SHA512": a.fabric_api_sha512,
        "VFG_PROFILE_JSON_URL": a.profile_url,
        "VFG_PROFILE_JSON_SHA256": a.profile_sha256,
        "VFG_BUILT_AT": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    check(values)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sh = fill((TEMPLATES / "install.sh").read_text("ascii"), values, bash_quote, "install.sh")
    ps1 = fill((TEMPLATES / "install.ps1").read_text("ascii"), values, ps_quote, "install.ps1")
    cmd_head = (TEMPLATES / "install.cmd").read_text("ascii")
    write(out / "vfg-install.sh", sh)
    write(out / "vfg-install.ps1", ps1)
    # cmd.exe wants CRLF; PowerShell reads either.
    write(out / "vfg-install.cmd", cmd_head + ps1, "\r\n")
    (out / "vfg-install.sh").chmod(0o700)


def page(a):
    expires = datetime.datetime.fromtimestamp(int(a.expires), datetime.timezone.utc)
    values = {
        "VFG_SERVER_ADDRESS": a.address,
        "VFG_MC_VERSION": a.mc_version,
        "VFG_SH_URL": a.sh_url,
        "VFG_CMD_URL": a.cmd_url,
        "VFG_PS1_URL": a.ps1_url,
        "VFG_PS_COMMAND": "irm '%s' | iex" % a.ps1_url.replace("'", "''"),
        "VFG_EXPIRES_ISO": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "VFG_EXPIRES_TEXT": expires.strftime("%A %d %B %Y, %H:%M UTC"),
    }
    check(values)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    text = fill((TEMPLATES / "page.html").read_text("utf-8"), values, html_quote, "page.html")
    (out / "index.html").write_bytes(text.encode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("installers")
    for flag in ("out", "address", "mc-version", "loader-version", "jar-name", "jar-url", "jar-sha256",
                 "fabric-api-name", "fabric-api-url", "fabric-api-sha512", "profile-url", "profile-sha256"):
        i.add_argument("--" + flag, required=True)
    p = sub.add_parser("page")
    for flag in ("out", "address", "mc-version", "sh-url", "cmd-url", "ps1-url", "expires"):
        p.add_argument("--" + flag, required=True)
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9.-]+(:[0-9]{1,5})?", a.address):
        die("--address %r is not a host name or IPv4 address" % a.address)
    installers(a) if a.cmd == "installers" else page(a)


if __name__ == "__main__":
    main()
