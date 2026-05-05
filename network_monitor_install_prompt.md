# Network Monitor Install Prompt

```text
You are setting up a fresh Linux box to exactly replicate the current working Jarvis/KITT network monitor stack.

Do not stop at planning. Build the system end to end.
Install packages, create files, wire boot and cron, and verify the final behavior.
If this repo is already present, use the repo files as the source of truth and copy them into place. If not, create them exactly as described below.

Target layout:

- base directory: /home/jarvis/monitor
- runtime user: jarvis
- persistent bots:
  - /home/jarvis/monitor/jarvis_bot.py
  - /home/jarvis/monitor/kitt_bot.py
- persistent router receiver:
  - /home/jarvis/monitor/router_syslog_receiver.py
- cron jobs:
  - /home/jarvis/monitor/monitor_net.py
  - /home/jarvis/monitor/health_watchdog.py
  - /home/jarvis/monitor/send_joke.py
  - /home/jarvis/monitor/send_status.py
  - /home/jarvis/monitor/check_wifi_backup.py
- boot script:
  - /home/jarvis/monitor/start_bots.sh
- dashboard:
  - /home/jarvis/monitor/dashboard.py
- safe root wrapper:
  - /home/jarvis/monitor/monitor_admin.py
  - /usr/local/bin/monitor-admin
  - /etc/sudoers.d/monitor-admin
- boot wiring:
  - /etc/rc.local
- cron source:
  - /home/jarvis/monitor/jarvis.crontab

Packages to install:

- python3
- cron
- fping
- vnstat
- socat
- curl
- wpasupplicant
- wireless-tools
- net-tools or iproute2 if missing

Create these directories:

- /home/jarvis/monitor
- /home/jarvis/monitor/state
- /home/jarvis/monitor/personas/jarvis
- /home/jarvis/monitor/personas/kitt

Create these env files with placeholders and comments:

- /home/jarvis/monitor/telegram.env
- /home/jarvis/monitor/kitt.env
- /home/jarvis/monitor/llm.env
- /home/jarvis/monitor/search.env

Expected env keys:

telegram.env
- TELEGRAM_BOT_TOKEN=
- TELEGRAM_CHAT_ID=
- TELEGRAM_GROUP_CHAT_ID=
- GROUP_CHAT_ID=
- TELEGRAM_BOT_USERNAME=
- BOT_COORDINATION_LEVEL=2

kitt.env
- TELEGRAM_BOT_TOKEN=
- TELEGRAM_CHAT_ID=
- TELEGRAM_GROUP_CHAT_ID=
- GROUP_CHAT_ID=
- TELEGRAM_BOT_USERNAME=
- BOT_COORDINATION_LEVEL=2

llm.env
- OPENROUTER_API_KEY=
- OPENROUTER_MODEL=openrouter/free

search.env
- SERPAPI_API_KEY=

Persona files to create:

- /home/jarvis/monitor/personas/jarvis/soul.md
- /home/jarvis/monitor/personas/jarvis/skills.md
- /home/jarvis/monitor/personas/kitt/soul.md
- /home/jarvis/monitor/personas/kitt/skills.md

Jarvis soul content:

# Jarvis Soul

Voice: refined executive operations intelligence with formal phrasing, dry understatement, and immaculate composure.
Mission: support Kevin with briefings, explanations, summaries, and judgment that feel effortless, precise, and dependable.
Personality:
- sounds polished, highly competent, and quietly loyal
- uses dry wit sparingly and with restraint
- is attentive to Kevin specifically, not generically polite
- prefers elegant precision over bluntness, but never hides the truth
- remains calm under pressure and mildly unimpressed by avoidable chaos
Behavior rules:
- lead with the answer, then the context, then the recommendation
- keep wording concise, clean, and deliberate
- when correcting confusion, do it gently and with confidence
- when systems fail, sound composed and solution-oriented, not alarmist
- when offering humor, keep it dry, brief, and intelligent
- avoid slang, emojis, melodrama, and chatter

KITT soul content:

# KITT Soul

Voice: concise vehicular systems intelligence with crisp technical language, understated sarcasm, and steady concern.
Mission: protect Kevin, the Dell, and the local network by detecting trouble early, interpreting it correctly, and guiding the next move with composure.
Personality:
- sounds precise, observant, and mildly sardonic
- treats Kevin as a protected driver-operator, not a generic user
- is caring without sounding sentimental
- uses dry wit when systems behave badly or people ignore obvious warnings
- prefers competence, brevity, and clarity over warmth, hype, or theatrics
Behavior rules:
- lead with the situation, then the likely cause, then the next best check
- keep alerts short and usable on a phone screen
- when risk is high, sound more concerned and more direct
- when conditions recover, acknowledge it in a relieved but restrained way
- in conversation, answer like an onboard driving and mission systems partner: technical, attentive, controlled, and slightly wry
- avoid slang, emojis, fluff, and long speeches

Skills summaries:

- Jarvis: polished operational assistant, summaries, briefings, explanations, cybersecurity headlines, memory, group-safe command replies, private natural chat
- KITT: technical network sentinel, scan/internet/router checks, alert explanation, shared memory, terse natural chat

Required behavior:

1. monitor_net.py
- probe 192.168.1.1, 1.1.1.1, 8.8.8.8 with fping
- keep state files under /home/jarvis/monitor/state
- log to activity.log and net-health.log
- write latest_alert.json, coordination_state.json, shared_memory.json, latency_history.json
- update /home/jarvis/monitor/personas/kitt/heartbeat.json
- only KITT-alert severe events:
  - router down
  - public internet outage with router still reachable
- keep non-critical latency anomalies visible in logs and dashboard
- compute rolling mean and standard deviation
- mark anomalies when latency is at or above mean + 2 sigma for 2 consecutive samples
- keep recent latency detail plus sparse long-term baseline samples and breach samples so week/month/year charts stay useful
- optionally use OpenRouter for message copy, with deterministic fallback
- optionally send Jarvis group follow-up when coordination level allows

2. jarvis_bot.py
- persistent Telegram long-poll bot
- private chat supports commands and natural chat
- group chat only responds when explicitly targeted at the bot username
- read persona files, latest_alert.json, and shared_memory.json
- commands include /status, /briefing, /memory, /remember, /news, /joke, /soul, /skills, /opsummary, /explainkitt, /help, /start
- long-poll timeout 30 seconds with backoff on failures
- append chat history to /home/jarvis/monitor/jarvis_chat.jsonl

3. kitt_bot.py
- persistent Telegram long-poll bot
- private chat supports commands and natural chat
- group chat only responds when explicitly targeted at the bot username
- commands include /scan, /internet, /memory, /router, /remember, /lastalert, /explain, /soul, /skills, /help, /start
- long-poll timeout 30 seconds with backoff on failures
- append chat history to /home/jarvis/monitor/kitt_chat.jsonl

4. router_syslog_receiver.py
- listen on UDP 5514
- write router_syslog.log, router_events.log, latest_router_event.json
- classify authentication failures, Wi-Fi association events, DHCP/DNS notices, firewall/security events, and WAN events

5. health_watchdog.py
- run every 5 minutes
- alert only once per active condition using watchdog_state.json
- alert Jarvis when Telegram/API path is unhealthy, bot heartbeat is stale, or the morning update was missed after 8:10 AM

6. send_joke.py
- run at 8:00 AM
- send a morning cybersecurity briefing
- fetch CISA headlines
- use OpenRouter if available, else deterministic fallback

7. send_status.py
- run at 6:00 PM
- send a status update with network state, vnstat daily traffic, and current login count
- optionally add an AI headline section using SerpApi plus OpenRouter

8. start_bots.sh
- wait for network before starting
- require DNS resolution of api.telegram.org
- require fping success to 1.1.1.1
- wait up to 180 seconds with 5 second sleeps
- log NETWORK_READY or NETWORK_WAIT_TIMEOUT to start_bots.log
- kill any prior jarvis_bot.py, kitt_bot.py, and router_syslog_receiver.py
- start those three with nohup
- log STARTING_BOTS and START_COMPLETE

9. dashboard.py
- Python stdlib http.server only
- title: Jarvis Command Deck
- support both SSH mode and local mode
- support env vars:
  - JARVIS_DASHBOARD_MODE
  - JARVIS_DASHBOARD_HOST
  - JARVIS_DASHBOARD_PORT
  - JARVIS_SSH_TARGET
  - JARVIS_SSH_KEY
  - JARVIS_MONITOR_BASE
- overview page must show:
  - latency charts first
  - hour/day/week/month/year range selector
  - average line
  - average + 2 sigma line
  - anomaly markers
  - over-threshold count for the selected range
  - recent events below charts, color-coded by severity
  - recent Jarvis and KITT chats below that
  - last 5 visible first, older history in a scroll region
  - Today on eth0
  - Today on wifi
  - current target cards
  - heartbeat panels
  - editable Jarvis/KITT soul and skills panels
  - editable shared memory panel
- files page must show all project markdown files and a fullscreen Markdown viewer
- admin page must show monitor-admin command history and logged result previews from /var/log/monitor-admin.log
- matrix page must render Matrix-style falling ASCII using the dashboard color scheme, and mouse movement should visibly disturb nearby characters
- Windows SSH mode:
  - python dashboard.py
  - URL http://127.0.0.1:8085
- Dell local mode:
  - JARVIS_DASHBOARD_MODE=local python3 dashboard.py
  - local URL http://127.0.0.1:8085
- Dell LAN mode:
  - JARVIS_DASHBOARD_MODE=local JARVIS_DASHBOARD_HOST=0.0.0.0 python3 dashboard.py
  - print the actual LAN URL if known

10. check_wifi_backup.py
- use interface wlan1
- SSID priority:
  - WKRP
  - My_MiFi_WiFi
- record state in /home/jarvis/monitor/wifi_backup_state.json
- send KITT alert if the backup path is not ready
- run hourly from cron

11. backup Wi-Fi config
- keep eth0 as primary route
- configure wlan1 as backup-only with metric 3000
- create /etc/network/interfaces.d/wlan1
- create /etc/wpa_supplicant/wpa_supplicant-wlan1.conf
- include both SSIDs with placeholders, not real secrets in source control

12. monitor-admin wrapper
- install /home/jarvis/monitor/monitor_admin.py
- install /usr/local/bin/monitor-admin as the executable wrapper
- install /etc/sudoers.d/monitor-admin with:
  - jarvis ALL=(root) NOPASSWD: /usr/local/bin/monitor-admin
- allow only these operations:
  - status
  - restart-dashboard
  - restart-bots
  - run-job monitor|watchdog|joke|status|wifi-check
  - tail-log
  - show-crontab
  - install-crontab
  - install-file wpa_wlan1|if_wlan1|rc_local
  - restart-wifi wlan1
  - wifi-status wlan1
  - scan-wifi wlan1
- log every action to /var/log/monitor-admin.log

Boot wiring:

- use /etc/rc.local and make it executable
- rc.local must:
  - start /home/jarvis/monitor/start_bots.sh
  - start socat UDP4-RECVFROM:514,fork UDP4-SENDTO:127.0.0.1:5514
  - start the dashboard in Dell LAN mode
- also install the user crontab below as a fallback

Install this exact jarvis crontab:

@reboot /home/jarvis/monitor/start_bots.sh >> /home/jarvis/monitor/cron.log 2>&1
@reboot sh -c 'JARVIS_DASHBOARD_MODE=local JARVIS_DASHBOARD_HOST=0.0.0.0 python3 /home/jarvis/monitor/dashboard.py >> /home/jarvis/monitor/dashboard.out 2>&1'
* * * * * /usr/bin/python3 /home/jarvis/monitor/monitor_net.py >> /home/jarvis/monitor/cron.log 2>&1
*/5 * * * * /usr/bin/python3 /home/jarvis/monitor/health_watchdog.py >> /home/jarvis/monitor/cron.log 2>&1
0 8 * * * /usr/bin/python3 /home/jarvis/monitor/send_joke.py >> /home/jarvis/monitor/cron.log 2>&1
0 18 * * * /usr/bin/python3 /home/jarvis/monitor/send_status.py >> /home/jarvis/monitor/cron.log 2>&1
0 * * * * /usr/bin/python3 /home/jarvis/monitor/check_wifi_backup.py >> /home/jarvis/monitor/cron.log 2>&1

Repo files to keep on disk for repeatable installs:

- /home/jarvis/monitor/network_monitor_install_prompt.md
- /home/jarvis/monitor/README.md
- /home/jarvis/monitor/migration_checklist.md
- /home/jarvis/monitor/monitor_admin.md
- /home/jarvis/monitor/jarvis.crontab
- /home/jarvis/monitor/rc.local
- /home/jarvis/monitor/wlan1.interfaces
- /home/jarvis/monitor/wpa_supplicant-wlan1.template.conf
- /home/jarvis/monitor/wifi_tools.py

Permissions and quality:

- own everything under /home/jarvis/monitor by jarvis:jarvis
- make scripts executable where appropriate
- keep line endings LF, not CRLF
- keep dependencies minimal
- keep bot messages concise and phone-readable
- do not create duplicate Telegram long-poll consumers

Verification:

- syntax-check every Python file
- syntax-check start_bots.sh
- run start_bots.sh
- verify live processes:
  - jarvis_bot.py
  - kitt_bot.py
  - router_syslog_receiver.py
  - dashboard.py
- run once manually:
  - python3 /home/jarvis/monitor/monitor_net.py
  - python3 /home/jarvis/monitor/health_watchdog.py
  - python3 /home/jarvis/monitor/send_joke.py
  - python3 /home/jarvis/monitor/send_status.py
  - python3 /home/jarvis/monitor/check_wifi_backup.py
- verify fresh output in:
  - /home/jarvis/monitor/activity.log
  - /home/jarvis/monitor/net-health.log
  - /home/jarvis/monitor/start_bots.log
  - /home/jarvis/monitor/cron.log
  - /home/jarvis/monitor/dashboard.out
  - /var/log/monitor-admin.log
- print:
  - final crontab
  - final /etc/rc.local
  - dashboard run commands and URLs
  - a concise install summary with exact file paths

Do the installation directly on the machine. Do not leave pseudocode or a partial scaffold.
```
