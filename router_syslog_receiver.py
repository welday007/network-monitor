#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import json
import re
import socket

BASE = Path('/home/jarvis/monitor')
RAW_LOG = BASE / 'router_syslog.log'
EVENT_LOG = BASE / 'router_events.log'
ACTIVITY_LOG = BASE / 'activity.log'
LATEST_EVENT = BASE / 'latest_router_event.json'
LISTEN_HOST = '0.0.0.0'
LISTEN_PORT = 5514
BUFFER_SIZE = 65535


def activity(event: str, **fields) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extras = ' '.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in fields.items())
    with ACTIVITY_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} script=router_syslog_receiver event={event}' + (f' {extras}' if extras else '') + '\n')


def write_raw(message: str, addr: tuple[str, int]) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with RAW_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} src={addr[0]}:{addr[1]} {message}\n')


def write_event(payload: dict) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'{ts} type={payload["type"]} severity={payload["severity"]} summary={payload["summary"]}'
    with EVENT_LOG.open('a', encoding='utf-8') as fh:
        fh.write(line + '\n')
    LATEST_EVENT.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    activity('ROUTER_EVENT', event_type=payload['type'], severity=payload['severity'], summary=payload['summary'])


def classify(message: str) -> dict | None:
    lowered = message.lower()
    summary = ''
    event_type = 'router_notice'
    severity = 'info'

    if 'unsuccessful login' in lowered or 'login failed' in lowered or 'authentication failure' in lowered:
        event_type = 'login_failure'
        severity = 'warning'
        summary = 'Router reported an unsuccessful login attempt.'
    elif 'action=disassociate' in lowered:
        event_type = 'wifi_disconnect'
        severity = 'warning'
        sta = extract_first(message, r'STA=([0-9a-f:]+)')
        summary = f'Router reported a Wi-Fi device disassociation{f" for {sta}" if sta else ""}.'
    elif 'action=associate' in lowered:
        event_type = 'wifi_connect'
        severity = 'info'
        sta = extract_first(message, r'STA=([0-9a-f:]+)')
        summary = f'Router reported a Wi-Fi device association{f" for {sta}" if sta else ""}.'
    elif 'dhcp' in lowered or 'dnsmasq' in lowered:
        event_type = 'dhcp_dns'
        severity = 'info'
        summary = 'Router logged a DHCP or DNS service event.'
    elif 'firewall' in lowered or 'security' in lowered:
        event_type = 'security_event'
        severity = 'warning'
        summary = 'Router logged a firewall or security event.'
    elif 'wan' in lowered:
        event_type = 'wan_event'
        severity = 'warning'
        summary = 'Router logged a WAN status event.'
    elif 'lan dhcp' in lowered or 'dhcp log' in lowered:
        event_type = 'lan_dhcp'
        severity = 'info'
        summary = 'Router logged a LAN DHCP event.'

    if not summary:
        return None

    return {
        'time': datetime.now().isoformat(timespec='seconds'),
        'type': event_type,
        'severity': severity,
        'summary': summary,
        'message': message,
    }


def extract_first(message: str, pattern: str) -> str:
    match = re.search(pattern, message, re.IGNORECASE)
    return match.group(1) if match else ''


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LISTEN_HOST, LISTEN_PORT))
    activity('LISTENING', host=LISTEN_HOST, port=LISTEN_PORT)
    while True:
        data, addr = sock.recvfrom(BUFFER_SIZE)
        message = data.decode('utf-8', errors='replace').strip()
        if not message:
            continue
        write_raw(message, addr)
        payload = classify(message)
        if payload:
            write_event(payload)


if __name__ == '__main__':
    main()
