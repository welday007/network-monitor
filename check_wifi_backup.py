#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import json
import shutil
import subprocess
import urllib.parse
import urllib.request

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
    if STATE_FILE.exists():
        try:
            payload = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                payload.setdefault('last_status', '')
                payload.setdefault('last_checked_at', '')
                return payload
        except Exception:
            pass
    return {'last_status': '', 'last_checked_at': ''}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')


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

    ping = run([fping_cmd, '-c1', '-t1500', '-I', INTERFACE, PING_TARGET])
    ping_ok = ping.returncode == 0

    return {
        'state': state,
        'addresses': addresses,
        'associated': bool(associated_ssid),
        'ssid': associated_ssid,
        'ping_ok': ping_ok,
        'route': route_text,
        'wifi_text': wifi_text,
    }


def build_alert(details: dict) -> str:
    summary = f'KITT: Backup Wi-Fi check failed on {INTERFACE}.'
    cause = f"Expected backup SSIDs {', '.join(SSIDS)} are not ready for use."
    observed = f"Observed: link={details['state']}, ssid={details.get('ssid','') or 'none'}, ip={'yes' if details['addresses'] else 'no'}, ping={details['ping_ok']}."
    return '\n'.join([summary, cause, observed])


def main() -> int:
    env = load_env(ENV_FILE)
    token = env.get('TELEGRAM_BOT_TOKEN')
    private_chat_id = env.get('TELEGRAM_CHAT_ID')
    group_chat_id = env.get('TELEGRAM_GROUP_CHAT_ID') or env.get('GROUP_CHAT_ID')
    state = load_state()
    details = interface_state()
    ok = details['state'] == 'UP' and details['associated'] and bool(details['addresses']) and details['ping_ok']
    status = 'OK' if ok else 'FAIL'
    state['last_status'] = status
    state['last_checked_at'] = datetime.now().isoformat(timespec='seconds')
    save_state(state)
    activity('WIFI_BACKUP_CHECK', status=status, interface=INTERFACE, associated=details['associated'], ssid=details.get('ssid', ''), addresses=details['addresses'], ping_ok=details['ping_ok'])
    log(f'WIFI_BACKUP_CHECK status={status} interface={INTERFACE} ssid={details.get("ssid", "") or "none"} ip={"yes" if details["addresses"] else "no"} ping_ok={details["ping_ok"]}')
    if ok or not token:
        return 0
    message = build_alert(details)
    for destination in configured_destinations(private_chat_id, group_chat_id):
        send_telegram(token, destination, message)
    activity('WIFI_BACKUP_ALERT_SENT', interface=INTERFACE, ssids=SSIDS)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
