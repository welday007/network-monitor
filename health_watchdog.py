#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import json
import urllib.parse
import urllib.request

BASE = Path('/home/jarvis/monitor')
TELEGRAM_ENV = BASE / 'telegram.env'
ACTIVITY_LOG = BASE / 'activity.log'
NET_LOG = BASE / 'net-health.log'
STATE_FILE = BASE / 'watchdog_state.json'
HEARTBEATS = {
    'jarvis': BASE / 'personas/jarvis/heartbeat.json',
    'kitt': BASE / 'personas/kitt/heartbeat.json',
}
HEARTBEAT_STALE_MINUTES = 20


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


def activity(event: str, **fields) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extras = ' '.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in fields.items())
    with ACTIVITY_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} script=health_watchdog event={event}' + (f' {extras}' if extras else '') + '\n')


def send_telegram(token: str, chat_id: str, text: str) -> None:
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': text, 'disable_web_page_preview': 'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            payload = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                payload.setdefault('alerts', {})
                payload.setdefault('heartbeat_fail_counts', {})
                return payload
        except Exception:
            pass
    return {'alerts': {}, 'heartbeat_fail_counts': {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')


def alert_once(state: dict, key: str, token: str, chat_id: str, message: str) -> None:
    if state['alerts'].get(key):
        return
    send_telegram(token, chat_id, message)
    state['alerts'][key] = {'sent_at': datetime.now().isoformat(timespec='seconds'), 'message': message}
    activity('ALERT_SENT', key=key, message=message)


def clear_alert(state: dict, key: str) -> None:
    if key in state['alerts']:
        del state['alerts'][key]
        activity('ALERT_CLEARED', key=key)


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def heartbeat_payload(path: Path) -> dict:
    if not path.exists():
        return {'status': 'missing', 'updated_at': ''}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'status': 'invalid', 'updated_at': ''}


def has_today_log(marker: str) -> bool:
    if not NET_LOG.exists():
        return False
    today = datetime.now().strftime('%Y-%m-%d')
    return any(line.startswith(today) and marker in line for line in NET_LOG.read_text(encoding='utf-8', errors='ignore').splitlines())


def telegram_health() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen('https://api.telegram.org', timeout=10) as resp:
            return True, str(resp.status)
    except Exception as exc:
        return False, f'{type(exc).__name__}: {exc}'


def main() -> None:
    env = load_env(TELEGRAM_ENV)
    token = env.get('TELEGRAM_BOT_TOKEN')
    chat_id = env.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return

    state = load_state()
    now = datetime.now()
    activity('RUN_START')

    ok, detail = telegram_health()
    if ok:
        clear_alert(state, 'telegram_down')
    else:
        alert_once(state, 'telegram_down', token, chat_id, f'Jarvis watchdog: Telegram/API path is unhealthy. Detail: {detail}')

    for name, path in HEARTBEATS.items():
        payload = heartbeat_payload(path)
        updated = parse_time(str(payload.get('updated_at', '')))
        status = str(payload.get('status', 'unknown'))
        stale = not updated or (now - updated).total_seconds() > HEARTBEAT_STALE_MINUTES * 60
        key = f'{name}_heartbeat'
        fail_counts = state.setdefault('heartbeat_fail_counts', {})
        count = int(fail_counts.get(key, 0))
        if status != 'ok' or stale:
            fail_counts[key] = count + 1
            reason = f'status={status}'
            if stale:
                reason += ', stale heartbeat'
            if fail_counts[key] >= 2:
                alert_once(state, key, token, chat_id, f'Jarvis watchdog: {name} bot health degraded ({reason}).')
        else:
            fail_counts[key] = 0
            clear_alert(state, key)

    if now.hour > 8 or (now.hour == 8 and now.minute >= 10):
        if not has_today_log('MORNING_SENT'):
            alert_once(state, 'morning_missed', token, chat_id, 'Jarvis watchdog: the 8:00 AM morning update appears to have been missed today.')
        else:
            clear_alert(state, 'morning_missed')

    save_state(state)
    activity('RUN_COMPLETE')


if __name__ == '__main__':
    main()
