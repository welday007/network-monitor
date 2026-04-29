#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import json
import shutil
import subprocess
import urllib.parse
import urllib.request

from wifi_tools import (
    alert_key,
    default_wifi_state,
    load_json_dict,
    readiness_detail,
    save_json_dict,
    scan_visible_ssids,
    should_send_alert,
)

BASE = Path('/home/jarvis/monitor')
LOG = BASE / 'net-health.log'
ACTIVITY_LOG = BASE / 'activity.log'
ENV_FILE = BASE / 'kitt.env'
STATE_FILE = BASE / 'wifi_backup_state.json'
SSIDS = ['WKRP', 'My_MiFi_WiFi']
INTERFACE = 'wlan1'
PING_TARGET = '1.1.1.1'
def now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def log(message: str) -> None:
    with LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{now()} {message}\n')


def activity(event: str, **fields) -> None:
    extras = ' '.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in fields.items())
    with ACTIVITY_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{now()} script=check_wifi_backup event={event}' + (f' {extras}' if extras else '') + '\n')


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        env[key.strip()] = value.strip()
    return env


def load_state() -> dict:
    return load_json_dict(STATE_FILE, default_wifi_state())


def save_state(state: dict) -> None:
    save_json_dict(STATE_FILE, state)


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def command_path(name: str, *fallbacks: str) -> str:
    for candidate in (shutil.which(name), *fallbacks):
        if candidate:
            return candidate
    return name


def send_telegram(token: str, chat_id: str, message: str) -> None:
    activity('SEND_ATTEMPT', chat_id=chat_id, chars=len(message))
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    activity('SEND_OK', chat_id=chat_id, chars=len(message))


def configured_destinations(*chat_ids: str | None) -> list[str]:
    ordered = []
    for chat_id in chat_ids:
        if chat_id and chat_id not in ordered:
            ordered.append(chat_id)
    return ordered


def interface_state() -> dict:
    ip_cmd = command_path('ip', '/usr/sbin/ip', '/sbin/ip')
    iwconfig_cmd = command_path('iwconfig', '/usr/sbin/iwconfig', '/sbin/iwconfig')
    iwlist_cmd = command_path('iwlist', '/usr/sbin/iwlist', '/sbin/iwlist')
    fping_cmd = command_path('fping', '/usr/sbin/fping', '/sbin/fping', '/usr/bin/fping')

    link = run([ip_cmd, '-brief', 'addr', 'show', INTERFACE])
    line = (link.stdout or '').strip()
    fields = line.split()
    state = fields[1] if len(fields) >= 2 else 'UNKNOWN'
    addresses = [field for field in fields[2:] if '/' in field]

    route = run([ip_cmd, 'route', 'show', 'dev', INTERFACE])
    route_text = (route.stdout or '').strip()

    wifi = run([iwconfig_cmd, INTERFACE])
    wifi_text = ((wifi.stdout or '') + (wifi.stderr or '')).strip()
    associated_ssid = next((ssid for ssid in SSIDS if ssid in wifi_text), '')

    visible_ssids, scan_text = scan_visible_ssids(iwlist_cmd, INTERFACE, SSIDS, run)
    matched_visible_ssid = next((ssid for ssid in SSIDS if ssid in visible_ssids), '')

    ping = run([fping_cmd, '-c1', '-t1500', '-I', INTERFACE, PING_TARGET])
    ping_ok = ping.returncode == 0
    scan_ready = bool(matched_visible_ssid)
    associated = bool(associated_ssid)
    ip_ready = bool(addresses)
    online_ready = state == 'UP' and scan_ready and associated and ip_ready and ping_ok
    status_detail = readiness_detail(state, scan_ready, associated, ip_ready, ping_ok)

    return {
        'state': state,
        'addresses': addresses,
        'associated': associated,
        'ssid': associated_ssid,
        'visible': scan_ready,
        'scan_ready': scan_ready,
        'visible_ssid': matched_visible_ssid,
        'visible_ssids': visible_ssids,
        'scan_text': scan_text,
        'ping_ok': ping_ok,
        'ip_ready': ip_ready,
        'online_ready': online_ready,
        'backup_ready': online_ready,
        'status_detail': status_detail,
        'route': route_text,
        'wifi_text': wifi_text,
    }


def build_alert(details: dict) -> str:
    summary = f'KITT: Backup Wi-Fi check failed on {INTERFACE}.'
    detail = details.get('status_detail')
    if detail == 'link_down':
        cause = 'wlan1 is down, so the backup adapter itself is not available.'
        next_check = 'Check the USB adapter, driver, and interface state.'
    elif detail == 'visible_not_associated':
        cause = f"Backup SSIDs {', '.join(SSIDS)} are visible, but wlan1 is not associated."
        next_check = 'Restart wlan1 and review wpa_cli status if association does not follow.'
    elif detail == 'visible_associated_no_ip':
        cause = 'wlan1 is associated, but it did not acquire an IP address.'
        next_check = 'Check dhcpcd, the DHCP lease, and the route on wlan1.'
    elif detail == 'visible_associated_no_ping':
        cause = 'wlan1 is associated and addressed, but the uplink is not responding.'
        next_check = 'Check the access point and upstream connectivity.'
    else:
        cause = f"Expected backup SSIDs {', '.join(SSIDS)} were not visible in scan results."
        next_check = 'Move closer to the access point or verify the backup SSID is actually broadcasting.'
    observed = (
        f"Observed: link={details['state']}, scan_ready={details.get('scan_ready', False)}, "
        f"online_ready={details.get('online_ready', False)}, visible={details.get('visible_ssids', []) or ['none']}, "
        f"current={details.get('ssid','') or 'none'}, ip={'yes' if details['ip_ready'] else 'no'}, "
        f"ping={details['ping_ok']}, detail={details.get('status_detail', 'unknown')}, "
        f"last_good={details.get('last_good_at', 'unknown')}, last_failure={details.get('last_failure_at', 'unknown')}."
    )
    return '\n'.join([summary, cause, observed, f'Recommended check: {next_check}'])


def main() -> int:
    env = load_env(ENV_FILE)
    token = env.get('TELEGRAM_BOT_TOKEN')
    private_chat_id = env.get('TELEGRAM_CHAT_ID')
    group_chat_id = env.get('TELEGRAM_GROUP_CHAT_ID') or env.get('GROUP_CHAT_ID')
    state = load_state()
    details = interface_state()
    ok = details['backup_ready']
    status = 'OK' if ok else 'FAIL'
    state['last_status'] = status
    state['last_checked_at'] = datetime.now().isoformat(timespec='seconds')
    state['last_detail'] = details.get('status_detail', '')
    state['last_visible_ssids'] = details.get('visible_ssids', [])
    state['last_associated_ssid'] = details.get('ssid', '')
    state['last_scan_ready'] = details.get('scan_ready', False)
    state['last_backup_ready'] = details.get('backup_ready', False)
    state['last_online_ready'] = details.get('online_ready', False)
    state['last_ip_ready'] = details.get('ip_ready', False)
    state['last_ping_ok'] = details.get('ping_ok', False)
    state['last_wifi_text'] = details.get('wifi_text', '')
    state['last_route'] = details.get('route', '')
    state['last_scan_text'] = details.get('scan_text', '')
    state['last_scan_raw'] = details.get('scan_text', '')
    if status == 'OK':
        state['last_good_at'] = state['last_checked_at']
        state['last_alert_key'] = ''
    else:
        state['last_failure_at'] = state['last_checked_at']
    details['last_good_at'] = state.get('last_good_at', '')
    details['last_failure_at'] = state.get('last_failure_at', '')
    save_state(state)
    activity('WIFI_BACKUP_CHECK', status=status, interface=INTERFACE, scan_ready=details['scan_ready'], online_ready=details['backup_ready'], visible_ssids=details.get('visible_ssids', []), visible_ssid=details.get('visible_ssid', ''), ssid=details.get('ssid', ''), addresses=details['addresses'], ip_ready=details['ip_ready'], ping_ok=details['ping_ok'], detail=details.get('status_detail', ''), last_good_at=details.get('last_good_at', ''), last_failure_at=details.get('last_failure_at', ''))
    log(f'WIFI_BACKUP_CHECK status={status} interface={INTERFACE} detail={details.get("status_detail", "") or "unknown"} scan_ready={details.get("scan_ready", False)} online_ready={details.get("backup_ready", False)} visible={details.get("visible_ssids", []) or ["none"]} current={details.get("ssid", "") or "none"} ip={"yes" if details["ip_ready"] else "no"} ping_ok={details["ping_ok"]} last_good={details.get("last_good_at", "unknown") or "unknown"} last_failure={details.get("last_failure_at", "unknown") or "unknown"}')
    if ok or not token:
        return 0
    key = alert_key(details)
    if should_send_alert(state, key):
        message = build_alert(details)
        for destination in configured_destinations(private_chat_id, group_chat_id):
            send_telegram(token, destination, message)
        state['last_alert_key'] = key
        state['last_alert_sent_at'] = datetime.now().isoformat(timespec='seconds')
        save_state(state)
        activity('WIFI_BACKUP_ALERT_SENT', interface=INTERFACE, ssids=SSIDS, detail=details.get('status_detail', ''))
    else:
        activity('WIFI_BACKUP_ALERT_DEDUPED', interface=INTERFACE, ssids=SSIDS, detail=details.get('status_detail', ''), key=key, last_alert_sent_at=state.get('last_alert_sent_at', ''))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
