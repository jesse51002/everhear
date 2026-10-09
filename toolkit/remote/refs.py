# refs.py: vfg action refs, the server's own command references. ARGS:
#   {"mc": "/opt/mc", "op": "version"}                 prints the version name: 26.3
#   {"mc": "/opt/mc", "op": "commands", "limit": 290}  prints {"version": "26.3", "commands": <commands.json>}
#
# The version is the `name` in the version.json inside <mc>/server.jar. The command tree
# comes from Minecraft's data generator, run in a temp dir under /tmp that is also its
# working directory: the bundler unpacks its libraries there and log4j writes logs/ there,
# so nothing lands in <mc> beside the running server. The dir is removed however it ends.
# The tree is printed compact on one line, about 190 KB for 26.3.
import shutil
import subprocess
import tempfile
import zipfile

GENERATOR = "net.minecraft.data.Main"


def server_jar():
    jar = os.path.join(ARGS["mc"], "server.jar")
    if not os.path.isfile(jar):
        die(1, "no server.jar in %s" % ARGS["mc"])
    return jar


def version(jar):
    """The name in the jar's version.json, like 26.3."""
    try:
        with zipfile.ZipFile(jar) as z:
            info = json.loads(z.read("version.json").decode("utf-8"))
    except KeyError:
        die(1, "%s has no version.json" % jar)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        die(1, "cannot read %s: %s" % (jar, e))
    name = (info.get("name") or info.get("id")) if isinstance(info, dict) else None
    if not isinstance(name, str) or not name.strip():
        die(1, "the version.json in %s names no version" % jar)
    return name.strip()


def last_line(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            lines = [l.strip() for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
    except OSError:
        lines = []
    return lines[-1] if lines else "no output"


def command_tree(jar):
    limit = float(ARGS["limit"])
    tmp = tempfile.mkdtemp(prefix="vfg-refs-")
    try:
        log = os.path.join(tmp, "generator.log")
        argv = ["java", "-DbundlerMainClass=" + GENERATOR, "-jar", jar, "--reports", "--output", tmp]
        with open(log, "wb") as f:
            try:
                p = subprocess.run(argv, cwd=tmp, stdin=subprocess.DEVNULL, stdout=f,
                                   stderr=subprocess.STDOUT, timeout=limit)
            except FileNotFoundError:
                die(1, "java is not installed on EC2")
            except subprocess.TimeoutExpired:
                die(1, "the data generator did not finish within %g s" % limit)
        if p.returncode != 0:
            die(1, "the data generator exited %d: %s" % (p.returncode, last_line(log)))
        try:
            with open(os.path.join(tmp, "reports", "commands.json"), encoding="utf-8") as f:
                tree = json.load(f)
        except FileNotFoundError:
            die(1, "the data generator wrote no reports/commands.json")
        except ValueError as e:
            die(1, "the data generator's commands.json is not JSON: %s" % e)
        if not isinstance(tree, dict) or not isinstance(tree.get("children"), dict) or not tree["children"]:
            die(1, "the data generator's commands.json holds no commands")
        return tree
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    jar = server_jar()
    name = version(jar)
    if ARGS["op"] == "version":
        print(name)
    elif ARGS["op"] == "commands":
        tree = command_tree(jar)
        sys.stdout.write(json.dumps({"version": name, "commands": tree}, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    else:
        die(2, "unknown refs op %r" % ARGS["op"])


main()
