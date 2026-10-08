# Home Assistant

With `[homeassistant] enabled = true` in [`fleet.toml`](fleet.md), the fleet
daemon announces itself to Home Assistant over **MQTT discovery**: one device
(*Minecraft*, by default) with everything needed to run a fleet from a
dashboard — no custom integration, no YAML on the Home Assistant side.

mcsu speaks MQTT 3.1.1 itself (its own small client, still zero dependencies),
so the only requirement is a broker Home Assistant uses — normally the
**Mosquitto broker** app with the **MQTT** integration.

## Setup

1. In the Mosquitto app's configuration, add a login for mcsu (for example
   user `minecraft` with a long random password).
2. Put the password in a file readable only by the user running the daemon:

   ```bash
   install -m 600 /dev/stdin /srv/minecraft/.mqtt-password <<< 'the-password'
   ```

3. In `fleet.toml`:

   ```toml
   [homeassistant]
   enabled = true
   broker = "homeassistant.local"
   username = "minecraft"
   password_file = "/srv/minecraft/.mqtt-password"
   ```

4. Restart `mcsu fleet run`. The device appears under **Settings → Devices &
   services → MQTT**.

## Entities

`<node>` is the slug of `[fleet] name` (e.g. `name = "minecraft"` →
`minecraft`); entity ids default to `<component>.<node>_<key>`.

| Entity | Id (default) | What it shows / does |
|---|---|---|
| Active server | `select.<node>_active_server` | `Off` + every server's title. **Choosing a server switches to it** (countdown + backup + clean stop of the old one); `Off` stops the running server. |
| State | `sensor.<node>_state` | `stopped`, `starting`, `running`, `restarting` (crashed, the watchdog is bringing it back), `switching`, `stopping`, `crashed` (gave up) — see [States](fleet.md#states) |
| Players online | `sensor.<node>_players_online` | Count; attribute `players` = the names |
| Server | `sensor.<node>_version` | Loader and Minecraft version of the active server, e.g. `Forge 1.18.2` |
| Online since | `sensor.<node>_started` | Timestamp the active server finished starting |
| Last backup | `sensor.<node>_last_backup` | Newest backup archive (diagnostic) |
| Memory | `sensor.<node>_memory` | JVM resident memory in MiB (diagnostic; Linux) |
| Crash loop | `binary_sensor.<node>_crash_loop` | Problem: on when a server's watchdog gave up and it was left stopped |
| Last action | `sensor.<node>_last_action` | What the last command did (diagnostic) |
| Restart | `button.<node>_restart` | Warned restart of the running server |
| Back up now | `button.<node>_backup` | Immediate backup of the running server |
| Stop | `button.<node>_stop` | Same as choosing `Off` |
| Player name | `text.<node>_player` | A Java Edition name (3–16 letters, digits, `_`) for the two buttons below |
| Whitelist player | `button.<node>_whitelist_add` | Adds *Player name* to **every** server's whitelist |
| Remove from whitelist | `button.<node>_whitelist_remove` | Removes it from every server |

All entities share an availability topic with a last-will message: while the
daemon is not running they read **unavailable**.

## Topics

Under `<base_topic>/<node>/` (default `mcsu/<node>/`):

| Topic | Direction | Payload |
|---|---|---|
| `availability` | mcsu → HA | `online` / `offline` (retained; `offline` is the last-will) |
| `state` | mcsu → HA | The full [status document](fleet.md#the-status-document-statusjson) as JSON (retained) |
| `player` | mcsu → HA | The current *Player name* as JSON, `{"name": "Steve"}` (retained; JSON because an empty retained payload would delete the retained message) |
| `cmd/active` | HA → mcsu | A server title, or `Off` |
| `cmd/player` | HA → mcsu | A player name |
| `cmd/restart`, `cmd/backup`, `cmd/stop` | HA → mcsu | `PRESS` |
| `cmd/whitelist_add`, `cmd/whitelist_remove` | HA → mcsu | `PRESS` |

Discovery configs are published (retained) to
`<discovery_prefix>/<component>/mcsu_<node>/<key>/config` on every connection.

## Example: alert when the server dies unexpectedly

`restarting` means the server crashed (a planned restart reads `starting`):

```yaml
automation:
  - alias: "Minecraft server crashed"
    triggers:
      - trigger: state
        entity_id: sensor.minecraft_state
        to: "restarting"
    actions:
      - action: notify.notify
        data:
          title: "Minecraft server crashed"
          message: "{{ states('sensor.minecraft_last_action') }}"
```

## Example: alert on a crash loop

```yaml
automation:
  - alias: "Minecraft crash loop"
    triggers:
      - trigger: state
        entity_id: binary_sensor.minecraft_crash_loop
        to: "on"
    actions:
      - action: notify.notify
        data:
          title: "Minecraft server crashed"
          message: "The server kept crashing and was stopped: {{ states('sensor.minecraft_last_action') }}"
```
