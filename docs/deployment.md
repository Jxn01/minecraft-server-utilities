# Deployment

`mcsu run` is a foreground supervisor: it owns the Java process as a child and
shuts it down cleanly when it receives `SIGINT`/`SIGTERM` (or a `mcsu stop`
control request). That makes it easy to run under any service manager.

The repo ships ready-to-use templates in [`deploy/`](../deploy/).

---

## Linux — systemd

1. Install mcsu system-wide (or in a virtualenv) and create your server:

   ```bash
   sudo python3 -m pip install /path/to/minecraft-server-utilities
   sudo useradd --system --home /opt/minecraft --shell /usr/sbin/nologin minecraft
   sudo -u minecraft mcsu init --dir /opt/minecraft/survival --loader paper --mc-version 1.20.4
   sudo -u minecraft sh -c 'cd /opt/minecraft/survival && mcsu install'
   ```

2. Copy and edit the unit file:

   ```bash
   sudo cp deploy/mcsu@.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now mcsu@survival
   ```

   The template is parameterized by instance name. `mcsu@survival` expects the
   server at `/opt/minecraft/survival` (adjust `WorkingDirectory` in the unit).

3. Operate it:

   ```bash
   systemctl status mcsu@survival
   journalctl -u mcsu@survival -f          # live console
   sudo -u minecraft mcsu -c /opt/minecraft/survival/mcsu.toml cmd "say hi"
   ```

`Restart=on-failure` in the unit is a backstop; mcsu's own watchdog handles
in-process crash recovery first.

## Windows — NSSM

[NSSM](https://nssm.cc/) turns any program into a Windows service.

```powershell
# After `pip install` and `mcsu init` / `mcsu install` in C:\mc\survival
nssm install mcsu-survival "C:\Path\To\python.exe" "-m mcsu run"
nssm set mcsu-survival AppDirectory "C:\mc\survival"
nssm set mcsu-survival AppStdout "C:\mc\survival\logs\mcsu.out.log"
nssm set mcsu-survival AppStderr "C:\mc\survival\logs\mcsu.err.log"
nssm start mcsu-survival
```

To stop gracefully, NSSM sends the console a Ctrl-C/terminate which mcsu
handles; alternatively `mcsu -c C:\mc\survival\mcsu.toml stop`.

### Windows — Task Scheduler

Create a task that runs `python -m mcsu run` with "Start in" set to your server
directory, triggered "At startup", running whether or not a user is logged on.

## Docker

A minimal image is provided ([`deploy/Dockerfile`](../deploy/Dockerfile)). It
bundles a JRE + mcsu; mount your server directory as a volume:

```bash
docker build -t mcsu -f deploy/Dockerfile .
docker run -it --rm \
  -p 25565:25565 \
  -v "$PWD/survival:/data" \
  mcsu
```

The container runs `mcsu run` against `/data` (which must contain `mcsu.toml`).
Set `auto_accept_eula = true` in the config or pre-create `eula.txt`.

## Networking

- Open **25565/tcp** (or your `server-port`) for players.
- Keep **RCON (25575/tcp by default) bound to localhost** — never expose it to
  the internet. mcsu defaults `rcon.host` to `127.0.0.1`.
