# VFG installer — one link for players

The owner sends one link. A player opens it, downloads the installer for their computer, runs it,
and the VFG voice mod is installed and pointed at today's server: Fabric Loader, Fabric API,
the mod, the server address, the client token, a launcher installation called **VFG 26.3**
and the server in its server list. The only manual steps are running what they downloaded and,
in the game, picking a mic.

## For the owner: each session

After the box has its new IP and `VFG_SSH_HOST` in `~/.vfg/config` is that IP:

```sh
installer/publish.sh        # from the repo's root
```

It prints two lines, the link and when it expires (7 days), and nothing else. Send the link.
Next session, run it again and send the new link: the IP changes every launch, and running the
new installer is how a player picks up the new address. Players keep their mic and settings.

It takes the newest `client-mod/build/libs/vfg-client-*.jar` at the moment you publish (its
checksum is worked out then), so build the mod first when you have changed it.

The first run makes a private S3 bucket, `vfg-installer-<8 hex>` in `VFG_AWS_REGION`, and writes its
name to `~/.vfg/config` as `VFG_INSTALL_BUCKET`. Every later run reuses it.

| Setting (`~/.vfg/config` or the environment) | Default |
|---|---|
| `VFG_SSH_HOST`, `VFG_SSH_USER`, `VFG_SSH_KEY` | as for the deploy scripts |
| `VFG_AWS_REGION` | `us-east-2` |
| `VFG_INSTALL_BUCKET` | made and written back on the first run |
| `VFG_INSTALL_ADDRESS` | the address players join; `VFG_SSH_HOST` |
| `VFG_CLIENT_JAR_DIR` | `client-mod/build/libs` |

**AWS credentials.** `aws` from `PATH` with permission to create the bucket once
(`s3:CreateBucket`, `s3:PutLifecycleConfiguration`) and then `s3:ListBucket` (for
`head-bucket`), `s3:PutObject` and `s3:GetObject` on it. A presigned link works only as long
as the credentials that signed it: with an IAM user's access keys that is the full 7 days;
with SSO or an assumed role it ends when that session does, often within hours —
`publish.sh` warns when that is the case.

## What a player sees

A page with three steps:

1. **Download.** Windows gets `vfg-install.cmd`; Mac and Linux get `vfg-install.sh`. The page
   explains SmartScreen ("More info → Run anyway") and also offers the PowerShell line
   `irm '<link>' | iex`, which does the same and never meets SmartScreen.
2. **Run it.** Double-click on Windows; `bash ~/Downloads/vfg-install.sh` on Mac and Linux.
3. **Play.** Pick **VFG 26.3** in the launcher, join **VFG**, press **K** to pick the mic.

## What an installer does

Each step is the same in `vfg-install.sh` (bash 3.2+, with python3 or perl for JSON) and
`vfg-install.ps1` (Windows PowerShell 5.1 and PowerShell 7). Running one again only updates.

1. **Finds the Minecraft folder** the launcher made: `%APPDATA%\.minecraft`;
   `~/Library/Application Support/minecraft`; on Linux `~/.minecraft` and the Flatpak
   launcher's `~/.var/app/com.mojang.Minecraft/.minecraft` — every one of those with a
   `launcher_profiles.json` (or `launcher_profiles_microsoft_store.json`) gets the install, the
   same folder reached twice counts once. None: "install and run the Minecraft launcher once
   first", and it stops having changed nothing.
2. **Waits for the launcher to close**, if it is running: it rewrites its list of
   installations as it exits.
3. **Downloads and checks** the Fabric Loader profile JSON (sha256), Fabric API
   `0.162.0+26.3` (Modrinth's sha512) and the VFG jar (sha256) before installing anything.
   A bad checksum or an expired link stops it with nothing changed.
4. **Fabric Loader 0.19.5 without Java:** writes
   `versions/fabric-loader-0.19.5-26.3/fabric-loader-0.19.5-26.3.json` (exactly what Fabric's
   meta server serves, which is what the official installer writes) and the empty jar beside
   it. The launcher downloads 26.3 and the libraries itself on the first start.
5. **Its own game folder,** `instances/vfg-26.3/`, so other installations and their mods are
   untouched. Its `mods/` gets Fabric API and the VFG jar; older `vfg-client-*.jar` and
   `fabric-api-*.jar` there are removed; any other mod a player added stays.
6. **`config/vfg-client.json`** is merged: `server_address` and `client_token` are set, every
   other key is kept as the player left it — `mic_device`, `model` (a fresh install gets the
   mod's own default), `bridge_url`. A file that does not parse is kept as
   `vfg-client.json.bad`, as the mod itself does. On Linux and Mac it is mode 600.
7. **`servers.dat`** of that game folder gets `VFG` with today's address, first in the list;
   other servers and tags are kept byte for byte. If that ever fails, the installer says so and
   prints the address, and the page shows it too.
8. **The launcher installation** "VFG 26.3" (key `vfg-26.3`, `lastVersionId
   fabric-loader-0.19.5-26.3`, `gameDir` that folder) is added or updated in
   `launcher_profiles.json`, and in `launcher_profiles_microsoft_store.json` if that exists.
   Each is checked before anything changes and backed up first: `<file>.before-vfg` is the
   original from before the first run and is never overwritten; `<file>.vfg-bak` is the file
   as it was just before the latest run. A player's own changes to the installation (memory,
   Java arguments) survive a re-run. `lastUsed` is set to now, so the launcher selects it.
9. Prints "Done" and the three things left to do.

To undo it: delete the "VFG 26.3" installation in the launcher and the folder
`instances/vfg-26.3`.

## Security

The installer files carry the client token (`VFG_CLIENT_TOKEN`), the bridge's bearer token,
because the point is that a player has nothing to type. That is accepted for this prototype:

- The token only lets someone post speech lines to the bridge (docs/CONTRACTS.md §2); it
  does not reach the Minecraft server's console, RCON, the box or AWS.
- The bucket is private: Block Public Access stays on (S3's default for new buckets), there is
  no bucket policy and no ACL. Objects are reachable only through presigned links.
- Each publish goes under a new random prefix, and its links are unguessable signatures that
  expire after 7 days. The bucket deletes everything 8 days after it was written.
- Anyone the link is forwarded to can install and post lines. If a link leaks, change the token
  (`bridge/deploy/set-secret.sh VFG_CLIENT_TOKEN`) and publish again; old installers then
  install a token the bridge refuses.
- `publish.sh` never prints, logs or passes the token on a command line: it is read over SSH
  into a variable and handed to `build.py` on stdin. The built files live only in a private
  temporary folder that is removed on exit. The tests use a made-up token and check that it
  never reaches any output or log.

## Files

```text
publish.sh                 the one command: gather, build, upload, presign, print the link
build.py                   fills the templates (quoting each value for bash, PowerShell or HTML)
templates/install.sh       Linux and macOS installer; its JSON and NBT work in python3 or perl
templates/install.ps1      Windows installer (also served for irm | iex)
templates/install.cmd      the double-click wrapper; build.py appends install.ps1 below it, CRLF
templates/page.html        the page players open
tests/test_installer.py    publish.sh with a stand-in aws and ssh, then every installer on fake homes
```

## Tests

```sh
python3 installer/tests/test_installer.py
```

No network, no AWS, no SSH: a local web server stands in for S3, Fabric's meta server and
Modrinth, and stand-in `aws`, `ssh`, `pgrep`, `uname` and `sleep` go first on `PATH` (the
PowerShell cases get a `Get-Process` and a `Start-Sleep` the same way), so no test waits on a
clock. The cases run side by side, each with its own home folder; the whole suite takes about
5 s. The PowerShell cases need `pwsh` (PowerShell 7): the Linux tarball from PowerShell's
GitHub releases, unpacked under `~/.local/opt/powershell-<version>/`, is found by itself;
without it they are skipped.

Covered: publish (one bucket, made once and remembered, nothing public; one random prefix per
publish; content types; 7-day presigning; the page linking the presigned installers; newest jar;
a bad Fabric API checksum uploads nothing; the token never printed or logged); and for the bash
installer (each case with python3 in the Linux layout and with perl in the macOS one; the
Flatpak folder and both Linux folders) and the PowerShell installer (as `irm | iex` and through
the `.cmd` wrapper's own command line): a fresh install, both launcher-profile files, a config
with a mic set, a broken config, a re-run next session (new address, token and jar; a player's
servers, mic and profile tweaks kept), a re-run with the same link, a missing game folder, a
running launcher, a tampered download, an expired link and a broken launcher list.

Not covered here: Windows PowerShell 5.1 itself (the script avoids everything 5.1 lacks, and
`cmd.exe`'s own parsing of the wrapper), a real Mac, and real S3.
