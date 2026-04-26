# Migration Checklist

Goal: move the Jarvis/KITT monitor stack from the current 32-bit Dell to a fresh 64-bit antiX install with minimal rework.

## Before Reinstall

1. Put this repo on GitHub.
2. Keep secrets out of GitHub:
   - `/home/jarvis/monitor/telegram.env`
   - `/home/jarvis/monitor/kitt.env`
   - `/home/jarvis/monitor/llm.env`
   - `/home/jarvis/monitor/search.env`
   - local `wpa_supplicant-wlan1.conf`
3. Save current live Dell reference data somewhere private:
   - `crontab -l`
   - `/etc/rc.local`
   - `/etc/network/interfaces.d/wlan1`
   - `/home/jarvis/monitor/start_bots.sh`
   - `/var/log/monitor-admin.log`
4. Keep the current Dell running for comparison during the first day or two.

## Fresh 64-bit Dell

1. Install 64-bit antiX.
2. Create user `jarvis`.
3. Install Git and clone this repo to:
   - `/home/jarvis/monitor`
4. Restore the private env files manually.
5. Restore the Wi-Fi password manually into:
   - `/home/jarvis/monitor/wpa_supplicant-wlan1.conf`
6. Install it to:
   - `/etc/wpa_supplicant/wpa_supplicant-wlan1.conf`
7. Run the install prompt or skill to rebuild the machine exactly.

## Verify After Rebuild

1. Verify bots are running:
   - `jarvis_bot.py`
   - `kitt_bot.py`
   - `router_syslog_receiver.py`
2. Verify cron entries are present.
3. Verify dashboard works:
   - local
   - LAN
4. Verify the dashboard tabs:
   - Overview
   - Files
   - Admin
   - Matrix
5. Verify chat history panels and latency charts render.
6. Verify backup Wi-Fi check runs hourly and logs.
7. Verify KITT only alerts on severe events.
8. Verify `monitor-admin` works through sudoers.

## Cutover

1. Leave both systems available briefly if possible.
2. Compare:
   - dashboard output
   - Telegram behavior
   - cron logs
   - monitor-admin output
   - network alerts
3. Switch fully to the new Dell once behavior matches.
