# systemd units for `gispulse watch`

Templated unit files for running the v1.3.0 `gispulse watch` daemon under systemd.

## Files

| File | Purpose |
|---|---|
| `gispulse-watch@.service` | Templated unit. `%i` selects the GPKG instance. |
| `gispulse-watch.env.example` | Per-instance environment file template. |

## Install

System-wide (recommended for production):

```bash
# 1. Create a dedicated unprivileged user for the daemon
sudo useradd --system --shell /usr/sbin/nologin \
    --home-dir /var/lib/gispulse --create-home gispulse

# 2. Lay down the runtime directories
sudo install -d -o gispulse -g gispulse /var/lib/gispulse /var/log/gispulse
sudo install -d /etc/gispulse

# 3. Drop the unit + env template
sudo install -m 0644 gispulse-watch@.service /etc/systemd/system/
sudo install -m 0644 gispulse-watch.env.example /etc/gispulse/

# 4. Per instance: create the env file, GPKG, and rules
#    (here `parcels` is the instance name)
sudo cp /etc/gispulse/gispulse-watch.env.example /etc/gispulse/parcels.env
sudo $EDITOR /etc/gispulse/parcels.env
sudo cp /path/to/your.gpkg     /var/lib/gispulse/parcels.gpkg
sudo cp /path/to/your.rules.yaml /etc/gispulse/parcels.rules.yaml
sudo chown gispulse:gispulse /var/lib/gispulse/parcels.gpkg
sudo chown gispulse:gispulse /etc/gispulse/parcels.rules.yaml

# 5. Install change-tracking on the layer(s) you want to watch
sudo -u gispulse gispulse track install \
    /var/lib/gispulse/parcels.gpkg --all-layers

# 6. Enable + start
sudo systemctl daemon-reload
sudo systemctl enable --now gispulse-watch@parcels
```

User-mode (great for desktop / dev / single-user laptops):

```bash
mkdir -p ~/.config/systemd/user ~/.config/gispulse
install -m 0644 gispulse-watch@.service ~/.config/systemd/user/
cp gispulse-watch.env.example ~/.config/gispulse/parcels.env
$EDITOR ~/.config/gispulse/parcels.env

# Adjust the unit's EnvironmentFile to point at the user-config path:
mkdir -p ~/.config/systemd/user/gispulse-watch@.service.d
cat > ~/.config/systemd/user/gispulse-watch@.service.d/override.conf <<'EOF'
[Service]
EnvironmentFile=
EnvironmentFile=-%h/.config/gispulse/%i.env
EOF

systemctl --user daemon-reload
systemctl --user enable --now gispulse-watch@parcels
```

## Operate

```bash
# Status + last 50 log lines
systemctl status gispulse-watch@parcels
journalctl -u gispulse-watch@parcels -n 50

# Tail logs
journalctl -u gispulse-watch@parcels -f

# Reload the rules YAML (re-read on next tick — no restart needed)
$EDITOR /etc/gispulse/parcels.rules.yaml
# (no systemctl reload needed; the watcher's triggers_provider re-snapshots
#  rules each tick — but if you want a fully clean re-read of CLI flags,
#  do: systemctl restart gispulse-watch@parcels)

# Bring it down (drains in-flight rows before exit)
sudo systemctl stop gispulse-watch@parcels

# Disable (won't auto-start at boot)
sudo systemctl disable gispulse-watch@parcels
```

## Health checks

```bash
# Verify trigger health (run as the gispulse user — same db locks)
sudo -u gispulse gispulse track doctor /var/lib/gispulse/parcels.gpkg

# Drain any backlog before stopping the watcher (cron-friendly)
sudo -u gispulse gispulse watch /var/lib/gispulse/parcels.gpkg \
    --rules /etc/gispulse/parcels.rules.yaml --once --exit-zero-if-empty
```

## Multiple GPKGs on the same host

Each GPKG = one instance. Reuse the unit, change the instance name:

```bash
sudo systemctl enable --now gispulse-watch@parcels
sudo systemctl enable --now gispulse-watch@roads
sudo systemctl enable --now gispulse-watch@buildings
```

systemd handles each as an independent supervised process. Use `systemctl
list-units 'gispulse-watch@*'` to see them all.

## Resource limits

The default `MemoryMax=512M` and `TasksMax=64` in the unit are conservative
for a single-GPKG watcher with up to ~100 active rules. If you run dozens
of instances on one host, drop a system-wide override:

```bash
sudo mkdir -p /etc/systemd/system/gispulse-watch@.service.d
sudo tee /etc/systemd/system/gispulse-watch@.service.d/limits.conf <<'EOF'
[Service]
MemoryMax=256M
TasksMax=32
EOF
sudo systemctl daemon-reload
```

## Uninstall

```bash
# Per instance
sudo systemctl disable --now gispulse-watch@parcels
sudo rm /etc/gispulse/parcels.env

# Then unit + user
sudo rm /etc/systemd/system/gispulse-watch@.service
sudo systemctl daemon-reload
sudo userdel gispulse  # only after all instances are stopped
```

## See also

- `gispulse watch --help`
- `gispulse track doctor --help`
- [TRIGGERS_GUIDE.md](../../docs/TRIGGERS_GUIDE.md)
