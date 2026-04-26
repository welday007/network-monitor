#!/bin/sh
MON=/home/jarvis/monitor
WAIT_LOG="$MON/start_bots.log"
MAX_WAIT_SECONDS=180
SLEEP_SECONDS=5

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"$WAIT_LOG"
}

wait_for_network() {
  waited=0
  while [ "$waited" -lt "$MAX_WAIT_SECONDS" ]; do
    if /usr/bin/getent hosts api.telegram.org >/dev/null 2>&1 && /usr/bin/fping -c1 -t1500 1.1.1.1 >/dev/null 2>&1; then
      log "NETWORK_READY waited=${waited}s"
      return 0
    fi
    sleep "$SLEEP_SECONDS"
    waited=$((waited + SLEEP_SECONDS))
  done
  log "NETWORK_WAIT_TIMEOUT waited=${waited}s proceeding=true"
  return 0
}

wait_for_network

pkill -f '/home/jarvis/monitor/jarvis_bot.py' 2>/dev/null || true
pkill -f '/home/jarvis/monitor/kitt_bot.py' 2>/dev/null || true
pkill -f '/home/jarvis/monitor/router_syslog_receiver.py' 2>/dev/null || true

log "STARTING_BOTS"
nohup /usr/bin/python3 /home/jarvis/monitor/jarvis_bot.py >/home/jarvis/monitor/jarvis_bot.out 2>&1 </dev/null &
nohup /usr/bin/python3 /home/jarvis/monitor/kitt_bot.py >/home/jarvis/monitor/kitt_bot.out 2>&1 </dev/null &
nohup /usr/bin/python3 /home/jarvis/monitor/router_syslog_receiver.py >/home/jarvis/monitor/router_syslog_receiver.out 2>&1 </dev/null &
log "START_COMPLETE"
