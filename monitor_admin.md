# Monitor Admin

Safe root wrapper for the Dell.

Installed command:

- `sudo /usr/local/bin/monitor-admin`
- Windows helper: [invoke_monitor_admin.ps1](C:/Users/17044/OneDrive/Documents/Kevin/Paperclip/invoke_monitor_admin.ps1)

Purpose:

- avoid repeated `sudo` password prompts
- keep root access narrow and allowlisted
- support the operations we actually use on this box

Main actions:

- `sudo /usr/local/bin/monitor-admin status`
- `sudo /usr/local/bin/monitor-admin restart-dashboard`
- `sudo /usr/local/bin/monitor-admin restart-bots`
- `sudo /usr/local/bin/monitor-admin run-job wifi-check`
- `sudo /usr/local/bin/monitor-admin tail-log dashboard 60`
- `sudo /usr/local/bin/monitor-admin show-crontab`
- `sudo /usr/local/bin/monitor-admin install-crontab`
- `sudo /usr/local/bin/monitor-admin install-file wpa_wlan1`
- `sudo /usr/local/bin/monitor-admin install-file if_wlan1`
- `sudo /usr/local/bin/monitor-admin install-file rc_local`
- `sudo /usr/local/bin/monitor-admin restart-wifi wlan1`
- `sudo /usr/local/bin/monitor-admin wifi-status wlan1`
- `sudo /usr/local/bin/monitor-admin scan-wifi wlan1`

From this Windows workspace:

- `powershell -File C:\Users\17044\OneDrive\Documents\Kevin\Paperclip\invoke_monitor_admin.ps1 status`
- `powershell -File C:\Users\17044\OneDrive\Documents\Kevin\Paperclip\invoke_monitor_admin.ps1 restart-dashboard`
- `powershell -File C:\Users\17044\OneDrive\Documents\Kevin\Paperclip\invoke_monitor_admin.ps1 tail-log dashboard 60`

Notes:

- dashboard and bots are restarted as user `jarvis`, not as root
- `/etc` file installs are restricted to an allowlist
- all actions are logged to `/var/log/monitor-admin.log`
- `wifi-status wlan1` now includes parsed fields under `parsed`
- `restart-wifi wlan1` runs a follow-up `wifi-check` and returns that verdict
- `scan-wifi wlan1` reports `scan_ready` when `WKRP` or `My_MiFi_WiFi` is visible
- `wpa_wlan1` expects a private local file `wpa_supplicant-wlan1.conf`
- the tracked starter file is `wpa_supplicant-wlan1.template.conf`
- intended sudoers rule:
  - `jarvis ALL=(root) NOPASSWD: /usr/local/bin/monitor-admin`
