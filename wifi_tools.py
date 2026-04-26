from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re
import subprocess
from typing import Callable

WIFI_ALERT_DEDUP_SECONDS = 90 * 60


def default_wifi_state() -> dict:
    return {
        'last_status': '',
        'last_checked_at': '',
        'last_detail': '',
        'last_visible_ssids': [],
        'last_associated_ssid': '',
        'last_scan_ready': False,
        'last_backup_ready': False,
        'last_online_ready': False,
        'last_ip_ready': False,
        'last_ping_ok': False,
        'last_wifi_text': '',
        'last_route': '',
        'last_scan_text': '',
        'last_scan_raw': '',
        'last_alert_key': '',
        'last_alert_sent_at': '',
        'last_good_at': '',
        'last_failure_at': '',
    }


def load_json_dict(path: Path, defaults: dict | None = None) -> dict:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                if defaults:
                    merged = dict(defaults)
                    merged.update(payload)
                    return merged
                return payload
        except Exception:
            pass
    return dict(defaults) if defaults else {}


def save_json_dict(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def parse_visible_ssids(scan_text: str) -> list[str]:
    visible = []
    for line in scan_text.splitlines():
        if 'ESSID:' not in line:
            continue
        start = line.find('ESSID:"')
        if start == -1:
            continue
        start += len('ESSID:"')
        end = line.find('"', start)
        if end == -1:
            continue
        ssid = line[start:end]
        if ssid and ssid not in visible:
            visible.append(ssid)
    return visible


def scan_visible_ssids(
    iwlist_cmd: str,
    interface: str,
    ssids: list[str],
    run_command: Callable[[list[str]], subprocess.CompletedProcess],
    *,
    attempts: int = 2,
) -> tuple[list[str], str]:
    scans: list[str] = []
    for _ in range(max(1, attempts)):
        proc = run_command([iwlist_cmd, interface, 'scan'])
        scan_text = ((proc.stdout or '') + (proc.stderr or '')).strip()
        if scan_text:
            scans.append(scan_text)
        visible_ssids = parse_visible_ssids(scan_text)
        if any(ssid in visible_ssids for ssid in ssids):
            return visible_ssids, scan_text
    combined = '\n\n'.join(scans)
    return parse_visible_ssids(combined), combined


def readiness_detail(link_state: str, scan_ready: bool, associated: bool, ip_ready: bool, ping_ok: bool) -> str:
    if link_state != 'UP':
        return 'link_down'
    if not scan_ready:
        return 'not_visible'
    if not associated:
        return 'visible_not_associated'
    if not ip_ready:
        return 'visible_associated_no_ip'
    if not ping_ok:
        return 'visible_associated_no_ping'
    return 'ready'


def alert_key(details: dict) -> str:
    visible = ','.join(details.get('visible_ssids', []))
    return '|'.join([
        str(details.get('status_detail', '')),
        visible,
        str(details.get('ssid', '')),
        str(details.get('state', '')),
    ])


def parse_alert_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def should_send_alert(state: dict, key: str) -> bool:
    if state.get('last_alert_key') != key:
        return True
    sent_at = parse_alert_time(str(state.get('last_alert_sent_at', '')))
    if not sent_at:
        return True
    return (datetime.now() - sent_at).total_seconds() >= WIFI_ALERT_DEDUP_SECONDS


def parse_ip_brief(lines: list[str]) -> dict[str, object]:
    text = ' '.join(lines).strip()
    fields = text.split()
    addresses = [field for field in fields[2:] if '/' in field] if len(fields) >= 2 else []
    return {
        'raw': lines,
        'text': text,
        'state': fields[1] if len(fields) >= 2 else 'UNKNOWN',
        'addresses': addresses,
        'primary_address': addresses[0] if addresses else '',
    }


def parse_iwconfig(lines: list[str]) -> dict[str, object]:
    text = '\n'.join(lines).strip()
    patterns = {
        'ssid': r'ESSID:"([^"]*)"',
        'mode': r'Mode:([^\s]+)',
        'frequency': r'Frequency:([^\s]+)',
        'access_point': r'Access Point:\s*([^\s]+)',
        'bit_rate': r'Bit Rate=([^\s]+)',
        'link_quality': r'Link Quality=([^\s]+)',
        'signal_level': r'Signal level=([^\s]+)',
        'tx_power': r'Tx-Power=([^\s]+)',
    }
    parsed: dict[str, object] = {'raw': lines, 'text': text}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        parsed[key] = match.group(1) if match else ''
    ssid = str(parsed.get('ssid', ''))
    parsed['associated'] = bool(ssid and ssid not in {'off/any', 'any'})
    return parsed


def parse_kv_lines(lines: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in lines:
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        parsed[key.strip().lower()] = value.strip()
    return parsed
