# Jarvis Network Monitor

This repo is the clean source for the Jarvis/KITT Dell network monitor stack.

Core pieces:

- persistent Telegram bots: `jarvis_bot.py`, `kitt_bot.py`
- minute monitor: `monitor_net.py`
- 5-minute watchdog: `health_watchdog.py`
- morning/evening cron jobs: `send_joke.py`, `send_status.py`
- boot startup: `start_bots.sh`, [rc.local](/C:/Users/17044/OneDrive/Documents/Kevin/Paperclip/rc.local)
- dashboard: `dashboard.py`
- safe root wrapper: `monitor_admin.py`
- backup Wi-Fi check: `check_wifi_backup.py`

Dashboard:

- Overview: charts first, severity-colored recent events, recent bot chats, traffic cards, target status, heartbeats, editable persona files
- Files: Markdown browser with fullscreen viewer
- Admin: `monitor-admin` command history and result previews
- Matrix: ASCII rain page with mouse disturbance
- chart ranges: hour, day, week, month, year
- charts show average, `+2 sigma`, anomaly dots, and over-threshold counts

Run the dashboard from Windows:

```powershell
python dashboard.py
```

Open:

```text
http://127.0.0.1:8085
```

Requirements:

- Windows `ssh` in `PATH`
- key-based SSH working for host `jarvis`
- Python 3 on Windows

Run the dashboard on the Dell:

```bash
JARVIS_DASHBOARD_MODE=local python3 dashboard.py
```

Dell LAN mode:

```bash
JARVIS_DASHBOARD_MODE=local JARVIS_DASHBOARD_HOST=0.0.0.0 python3 dashboard.py
```

Current LAN URL:

```text
http://192.168.1.24:8085
```

Backup Wi-Fi:

- primary route stays on `eth0`
- USB adapter backup is `wlan1`
- SSID order: `WKRP`, then `My_MiFi_WiFi`
- hourly backup check: `check_wifi_backup.py`
- route metric on `wlan1`: `3000`

GitHub-safe Wi-Fi handling:

- tracked template: [wpa_supplicant-wlan1.template.conf](/C:/Users/17044/OneDrive/Documents/Kevin/Paperclip/wpa_supplicant-wlan1.template.conf)
- local private file to create before install: `wpa_supplicant-wlan1.conf`
- do not commit real Wi-Fi credentials

Key bootstrap files:

- [network_monitor_install_prompt.md](/C:/Users/17044/OneDrive/Documents/Kevin/Paperclip/network_monitor_install_prompt.md)
- [jarvis.crontab](/C:/Users/17044/OneDrive/Documents/Kevin/Paperclip/jarvis.crontab)
- [rc.local](/C:/Users/17044/OneDrive/Documents/Kevin/Paperclip/rc.local)
- [monitor_admin.md](/C:/Users/17044/OneDrive/Documents/Kevin/Paperclip/monitor_admin.md)
