#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import atexit
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from wifi_tools import default_wifi_state, load_json_dict

BASE = Path('/home/jarvis/monitor')
ENV_FILE = BASE / 'kitt.env'
LLM_ENV = BASE / 'llm.env'
STATE_FILE = BASE / 'kitt_bot_state.json'
LOG = BASE / 'net-health.log'
ACTIVITY_LOG = BASE / 'activity.log'
HEARTBEAT = BASE / 'personas/kitt/heartbeat.json'
KITT_SOUL = BASE / 'personas/kitt/soul.md'
KITT_SKILLS = BASE / 'personas/kitt/skills.md'
WIFI_STATE = BASE / 'wifi_backup_state.json'
SHARED_MEMORY = BASE / 'shared_memory.json'
LOCK_FILE = BASE / 'kitt_bot.pid'
CHAT_LOG = BASE / 'kitt_chat.jsonl'
TARGETS = ['192.168.1.1', '1.1.1.1', '8.8.8.8']
HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
GROUP_ALLOWED_COMMANDS = {
    '/scan',
    '/internet',
    '/memory',
    '/router',
    '/lastalert',
    '/explain',
    '/soul',
    '/skills',
    '/help',
    '/start',
}
BOT_USERNAME = 'kitt_welday_ent_bot'
BOT_DISPLAY_NAME = 'KITT'
LONG_POLL_TIMEOUT = 30
DEFAULT_MODEL = 'openrouter/free'
ERROR_BACKOFF_SECONDS = [5, 10, 20, 30, 60]

LOCK_ACQUIRED = False


def default_state() -> dict:
    return {
        'last_update_id': 0,
        'poll_error_active': False,
        'last_poll_error_reason': '',
        'last_poll_error_detail': '',
        'last_poll_error_at': '',
        'last_poll_recovered_at': '',
    }

def update_heartbeat(source: str, status: str = 'ok') -> None:
    HEARTBEAT.write_text(json.dumps({'bot': 'kitt', 'source': source, 'status': status, 'updated_at': datetime.now().isoformat(timespec='seconds')}, indent=2), encoding='utf-8')

def load_env(path: Path) -> dict:
    env = {}
    if not path.exists(): return env
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1)
        env[key.strip()] = value.strip()
    return env

def log(message: str) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with LOG.open('a', encoding='utf-8') as fh: fh.write(f'{ts} {message}\n')

def activity(event: str, **fields) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extras = ' '.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in fields.items())
    with ACTIVITY_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} script=kitt_bot event={event}' + (f' {extras}' if extras else '') + '\n')

def record_chat(direction: str, chat_id: str, chat_type: str, text: str) -> None:
    entry = {
        'time': datetime.now().isoformat(timespec='seconds'),
        'direction': direction,
        'chat_id': chat_id,
        'chat_type': chat_type,
        'text': text,
    }
    with CHAT_LOG.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, ensure_ascii=True) + '\n')

def telegram_api(token: str, method: str, data: dict | None = None, request_timeout: int = 20) -> dict:
    encoded = urllib.parse.urlencode(data or {}).encode() if data is not None else None
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}', data=encoded, method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=request_timeout) as resp: return json.loads(resp.read().decode('utf-8'))

def send_message(token: str, chat_id: str, text: str) -> None:
    activity('SEND_ATTEMPT', chat_id=chat_id, chars=len(text))
    telegram_api(token, 'sendMessage', {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': 'true'}, request_timeout=25)
    activity('SEND_OK', chat_id=chat_id, chars=len(text))
    record_chat('out', chat_id, 'unknown', text)

def normalize_command(text: str) -> str:
    raw = text.strip().split()[0].lower()
    return raw.split('@', 1)[0]

def normalize_target(text: str) -> str:
    return text.strip().lower().replace('@', '')

def contains_name(text: str, name: str) -> bool:
    return bool(re.search(rf'\b{re.escape(name.lower())}\b', text.lower()))

def command_target(text: str) -> str:
    raw = text.strip().split()[0].lower()
    parts = raw.split('@', 1)
    return parts[1] if len(parts) == 2 else ''

def configured_chat_ids(private_chat_id: str, group_chat_id: str) -> set[str]:
    return {chat for chat in (private_chat_id, group_chat_id) if chat}

def command_allowed_in_chat(cmd: str, target: str, chat_type: str, incoming_chat_id: str, private_chat_id: str, group_chat_id: str, bot_username: str) -> bool:
    if incoming_chat_id == private_chat_id:
        return True
    if group_chat_id and incoming_chat_id == group_chat_id:
        if cmd not in GROUP_ALLOWED_COMMANDS or chat_type not in {'group', 'supergroup'}:
            return False
        return target == bot_username.lower()
    return False

def load_state() -> dict:
    state = default_state()
    if STATE_FILE.exists():
        try:
            payload = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                state.update(payload)
        except Exception:
            pass
    return state

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding='utf-8')

def default_memory() -> dict:
    return {
        'updated_at': '',
        'updated_by': '',
        'summary': 'No shared memory stored yet.',
        'notes': [],
        'recent_events': [],
    }

def load_memory() -> dict:
    if SHARED_MEMORY.exists():
        try:
            payload = json.loads(SHARED_MEMORY.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                memory = default_memory()
                memory.update(payload)
                return memory
        except Exception:
            pass
    return default_memory()

def save_memory(memory: dict) -> None:
    memory['updated_at'] = datetime.now().isoformat(timespec='seconds')
    SHARED_MEMORY.write_text(json.dumps(memory, indent=2), encoding='utf-8')

def add_memory_note(source: str, note: str) -> None:
    memory = load_memory()
    notes = [item for item in memory.get('notes', []) if isinstance(item, dict)]
    notes.append({'time': datetime.now().isoformat(timespec='seconds'), 'source': source, 'text': note.strip()})
    memory['notes'] = notes[-10:]
    memory['updated_by'] = source
    memory['summary'] = note.strip()
    save_memory(memory)

def recent_memory_lines() -> str:
    memory = load_memory()
    lines = [f"Summary: {memory.get('summary', 'No summary stored yet.')}"]
    notes = [item for item in memory.get('notes', []) if isinstance(item, dict)][-3:]
    for item in notes:
        lines.append(f"Note: {item.get('source', 'unknown')} said {item.get('text', '')}")
    events = [item for item in memory.get('recent_events', []) if isinstance(item, dict)][-2:]
    for item in events:
        lines.append(f"Event: {item.get('summary', '')}")
    return '\n'.join(lines)

def acquire_lock() -> None:
    global LOCK_ACQUIRED
    current_pid = os.getpid()
    if LOCK_FILE.exists():
        try:
            existing_pid = int(LOCK_FILE.read_text(encoding='utf-8').strip())
        except Exception:
            existing_pid = 0
        if existing_pid and existing_pid != current_pid:
            try:
                os.kill(existing_pid, 0)
            except OSError:
                pass
            else:
                activity('DUPLICATE_INSTANCE', existing_pid=existing_pid)
                raise SystemExit(f'KITT bot already running with pid {existing_pid}')
    LOCK_FILE.write_text(str(current_pid), encoding='utf-8')
    LOCK_ACQUIRED = True


def release_lock() -> None:
    global LOCK_ACQUIRED
    if not LOCK_ACQUIRED:
        return
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding='utf-8').strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass
    LOCK_ACQUIRED = False

def probe(host: str) -> tuple[str, str]:
    proc = subprocess.run(['fping', '-c1', '-t1500', host], capture_output=True, text=True)
    output = (proc.stdout or '') + (proc.stderr or '')
    match = re.search(r'([0-9]+(?:\.[0-9]+)?) ms', output)
    return ('UP' if proc.returncode == 0 else 'DOWN', match.group(1) if match else 'NA')

def scan_text() -> str:
    lines = ['KITT scan complete.', '']
    for host in TARGETS:
        status, rtt = probe(host); lines.append(f'{host}: {status} ({rtt} ms)')
    lines.extend(['', 'Observation: The road appears clear enough for sensible decision-making.'])
    return '\n'.join(lines)

def internet_text() -> str:
    cf = probe('1.1.1.1'); gg = probe('8.8.8.8')
    if cf[0] == 'UP' and gg[0] == 'UP': verdict = 'External connectivity appears stable.'
    elif cf[0] == 'DOWN' and gg[0] == 'DOWN': verdict = 'Both public targets are unreachable. This suggests an upstream outage.'
    else: verdict = 'Internet reachability is mixed. Something upstream may be degraded.'
    return '\n'.join(['KITT internet check.', '', f'1.1.1.1: {cf[0]} ({cf[1]} ms)', f'8.8.8.8: {gg[0]} ({gg[1]} ms)', '', verdict])

def router_text() -> str:
    status, rtt = probe('192.168.1.1')
    return '\n'.join(['KITT router check.', '', f'Router 192.168.1.1: {status} ({rtt} ms)', '', 'Advisory: If the router is healthy while public targets fail, the trouble is likely beyond the house.'])

def lastalert_text() -> str:
    if not LOG.exists(): return 'KITT: No alert history is available yet.'
    lines = [line for line in LOG.read_text(encoding='utf-8', errors='ignore').splitlines() if any(tag in line for tag in ('STATE_CHANGE', 'LATENCY_ANOMALY', 'LATENCY_NORMALIZED', 'INTERNET_OUTAGE', 'ROUTER_DOWN'))]
    if not lines: return 'KITT: The alert log is currently uneventful. A rare pleasure.'
    return 'KITT last alert log.\n\n' + '\n'.join(lines[-5:])

def explain_text() -> str:
    if WIFI_STATE.exists():
        try:
            wifi_state = load_json_dict(WIFI_STATE, default_wifi_state())
            if isinstance(wifi_state, dict) and wifi_state.get('last_checked_at'):
                detail = wifi_state.get('last_detail', '')
                last_checked = wifi_state.get('last_checked_at', 'unknown time')
                visible = wifi_state.get('last_visible_ssids', [])
                ssid = wifi_state.get('last_associated_ssid', '')
                last_good = wifi_state.get('last_good_at', '')
                last_failure = wifi_state.get('last_failure_at', '')
                scan_ready = bool(wifi_state.get('last_scan_ready', False))
                online_ready = bool(wifi_state.get('last_backup_ready', False))
                ip_ready = bool(wifi_state.get('last_ip_ready', False))
                ping_ok = bool(wifi_state.get('last_ping_ok', False))
                visible_text = ', '.join(visible) if visible else 'none'
                if wifi_state.get('last_status') == 'OK' or online_ready:
                    return (
                        'KITT analysis.\n\n'
                        f'Backup Wi-Fi was last checked at {last_checked}. '
                        f'Scan ready: {scan_ready}. Online ready: {online_ready}. '
                        f'Visible SSIDs: {visible_text}. Associated SSID: {ssid or "none"}. '
                        f'Last good: {last_good or "unknown"}. Last failure: {last_failure or "unknown"}.'
                    )
                if detail == 'visible_not_associated':
                    return (
                        'KITT analysis.\n\n'
                        f'Backup SSIDs were visible at {last_checked}, but wlan1 was not associated. '
                        f'The radio can see the network, so this is a connection problem rather than a missing SSID. '
                        f'Last failure: {last_failure or "unknown"}.'
                    )
                if detail == 'visible_associated_no_ip':
                    return (
                        'KITT analysis.\n\n'
                        f'wlan1 saw the backup SSID at {last_checked} and associated, but no IP address was acquired. '
                        f'That points to DHCP or lease trouble. Last failure: {last_failure or "unknown"}.'
                    )
                if detail == 'visible_associated_no_ping':
                    return (
                        'KITT analysis.\n\n'
                        f'wlan1 associated and received an address at {last_checked}, but ping still failed. '
                        f'The uplink is likely impaired beyond the local radio link. Last failure: {last_failure or "unknown"}.'
                    )
                if detail == 'link_down':
                    return (
                        'KITT analysis.\n\n'
                        f'wlan1 itself was down at {last_checked}. '
                        f'That is an interface or driver problem, not a missing SSID. Last failure: {last_failure or "unknown"}.'
                    )
                return (
                    'KITT analysis.\n\n'
                    f'Backup Wi-Fi was last checked at {last_checked}. '
                    f'Scan ready: {scan_ready}. Online ready: {online_ready}. IP ready: {ip_ready}. Ping OK: {ping_ok}. '
                    f'Last good: {last_good or "unknown"}. Last failure: {last_failure or "unknown"}. '
                    'That points to coverage, radio, or SSID broadcast trouble.'
                )
        except Exception:
            pass
    if not LOG.exists(): return 'KITT: There is no recent alert to explain.'
    lines = [line for line in LOG.read_text(encoding='utf-8', errors='ignore').splitlines() if any(tag in line for tag in ('STATE_CHANGE', 'LATENCY_ANOMALY', 'LATENCY_NORMALIZED', 'INTERNET_OUTAGE', 'ROUTER_DOWN'))]
    if not lines: return 'KITT: There is no recent alert to explain.'
    last = lines[-1]
    if 'LATENCY_ANOMALY' in last:
        return 'KITT analysis.\n\nThat alert means one monitored target moved well outside its normal latency range. The line is up, but performance is abnormally degraded.'
    if 'LATENCY_NORMALIZED' in last:
        return 'KITT analysis.\n\nThat alert means the earlier abnormal latency cleared and the target returned to its usual range.'
    if 'INTERNET_OUTAGE' in last:
        return 'KITT analysis.\n\nThat alert means the router stayed reachable while both public targets failed. The problem is likely upstream, not inside the house.'
    if 'ROUTER_DOWN' in last:
        return 'KITT analysis.\n\nThat alert means the local router path failed. This points to a local gateway, cabling, or Wi-Fi issue.'
    if 'STATE_CHANGE' in last and 'status=DOWN' in last:
        return 'KITT analysis.\n\nThat alert means a monitored target stopped responding. If the router is still up while public targets fail, the problem is probably upstream, not inside the house.'
    if 'STATE_CHANGE' in last and 'status=UP' in last:
        return 'KITT analysis.\n\nThat alert means the target is responding again. In plain English, the connection recovered.'
    return 'KITT analysis.\n\nThe last alert indicates a change in network conditions that deserves a quick check.'

def natural_message_allowed(text: str, msg: dict, chat_type: str, incoming_chat_id: str, private_chat_id: str, group_chat_id: str, bot_username: str) -> bool:
    if incoming_chat_id == private_chat_id:
        return True
    if group_chat_id and incoming_chat_id == group_chat_id and chat_type in {'group', 'supergroup'}:
        lowered = normalize_target(text)
        reply_from = (msg.get('reply_to_message') or {}).get('from', {})
        reply_username = str(reply_from.get('username', '')).lower()
        return (
            bot_username in lowered
            or contains_name(text, BOT_DISPLAY_NAME)
            or reply_username == bot_username
        )
    return False

def request_natural_reply(user_text: str, chat_type: str) -> str:
    llm = load_env(LLM_ENV)
    api_key = llm.get('OPENROUTER_API_KEY')
    model = llm.get('OPENROUTER_MODEL', DEFAULT_MODEL)
    soul = KITT_SOUL.read_text(encoding='utf-8', errors='ignore').strip() if KITT_SOUL.exists() else ''
    skills = KITT_SKILLS.read_text(encoding='utf-8', errors='ignore').strip() if KITT_SKILLS.exists() else ''
    memory = recent_memory_lines()
    if not api_key:
        return "My conversational systems are presently offline. I can still provide direct operational checks through commands."
    system = (
        "You are KITT, a concise vehicular systems intelligence with dry wit, technical precision, and protective concern. "
        "Avoid direct imitation of any copyrighted fictional character. "
        "Answer ordinary messages conversationally, naturally, and briefly."
    )
    user_prompt = (
        f"Soul:\n{soul}\n\nSkills:\n{skills}\n\nShared memory:\n{memory}\n\n"
        f"Chat type: {chat_type}\n"
        f"User message: {user_text}\n\n"
        "Reply in plain text. Keep it concise and natural. Usually 1-4 short paragraphs."
    )
    payload = {'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user_prompt}], 'temperature': 0.65}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        return body['choices'][0]['message']['content'].strip()
    except Exception as exc:
        activity('LLM_ERROR', reason=type(exc).__name__, detail=str(exc))
        return "My language model link is unstable at the moment. A direct diagnostic command would be more reliable."

def memory_text() -> str:
    memory = load_memory()
    lines = ['Shared memory', '']
    lines.append(f"Updated: {memory.get('updated_at') or 'unknown'}")
    if memory.get('updated_by'):
        lines.append(f"Last writer: {memory.get('updated_by')}")
    lines.extend(['', f"Summary: {memory.get('summary', 'No summary stored.')}"])
    notes = [item for item in memory.get('notes', []) if isinstance(item, dict)][-3:]
    if notes:
        lines.extend(['', 'Recent notes:'])
        for item in notes:
            lines.append(f"- {item.get('time', '?')} {item.get('source', 'unknown')}: {item.get('text', '')}")
    events = [item for item in memory.get('recent_events', []) if isinstance(item, dict)][-2:]
    if events:
        lines.extend(['', 'Recent events:'])
        for item in events:
            lines.append(f"- {item.get('time', '?')} {item.get('source', 'monitor')}: {item.get('summary', '')}")
    return '\n'.join(lines)

def file_text(path: Path, title: str) -> str:
    if not path.exists(): return f'{title}\n\nNot configured.'
    return f'{title}\n\n' + path.read_text(encoding='utf-8', errors='ignore').strip()

def help_text() -> str:
    return '\n'.join(['KITT commands', '', '/scan - full quick scan', '/internet - public internet check', '/memory - shared bot memory', '/remember <note> - store a private memory note', '/router - local router check', '/lastalert - recent alert history', '/explain - what the last alert means', '/soul - current KITT persona summary', '/skills - current KITT skill list', '/help - this list', '', 'Group behavior: alert and command mode only. Private chat remains direct and separate.'])

def handle_command(text: str, incoming_chat_id: str, private_chat_id: str) -> str | None:
    cmd = normalize_command(text)
    if cmd == '/scan': return scan_text()
    if cmd == '/internet': return internet_text()
    if cmd == '/memory': return memory_text()
    if cmd == '/remember' and incoming_chat_id == private_chat_id:
        note = text.strip()[len(text.strip().split()[0]):].strip()
        if not note:
            return 'KITT memory\n\nPlease provide a note after /remember.'
        add_memory_note('kitt', note)
        return 'KITT memory\n\nStored in shared memory.'
    if cmd == '/router': return router_text()
    if cmd == '/lastalert': return lastalert_text()
    if cmd == '/explain': return explain_text()
    if cmd == '/soul': return file_text(KITT_SOUL, 'KITT soul')
    if cmd == '/skills': return file_text(KITT_SKILLS, 'KITT skills')
    if cmd in ('/help', '/start'): return help_text()
    return None

def run_once(token: str, private_chat_id: str, group_chat_id: str, bot_username: str, state: dict) -> bool:
    offset = int(state.get('last_update_id', 0)) + 1
    update_heartbeat('kitt_bot.py')
    poll_started = time.time()
    activity('POLL_BEGIN', offset=offset, timeout=LONG_POLL_TIMEOUT)
    response = telegram_api(token, 'getUpdates', {'offset': offset, 'timeout': LONG_POLL_TIMEOUT}, request_timeout=LONG_POLL_TIMEOUT + 15)
    activity('POLL_OK', elapsed_ms=int((time.time() - poll_started) * 1000), updates=len(response.get('result', [])))
    had_activity = False
    for update in response.get('result', []):
        state['last_update_id'] = update['update_id']
        msg = update.get('message') or {}
        chat = msg.get('chat', {})
        text = (msg.get('text') or '').strip()
        incoming_chat_id = str(chat.get('id', ''))
        chat_type = str(chat.get('type', ''))
        if incoming_chat_id not in configured_chat_ids(private_chat_id, group_chat_id): continue
        if text:
            record_chat('in', incoming_chat_id, chat_type, text)
        if text.startswith('/'):
            cmd = normalize_command(text)
            target = command_target(text)
            if not command_allowed_in_chat(cmd, target, chat_type, incoming_chat_id, private_chat_id, group_chat_id, bot_username): continue
            activity('COMMAND_RECEIVED', command=cmd, chat_id=incoming_chat_id, chat_type=chat_type)
            reply = handle_command(text, incoming_chat_id, private_chat_id)
            if reply:
                send_message(token, incoming_chat_id, reply)
                had_activity = True
                log(f'KITT_COMMAND_HANDLED command={cmd} chat={incoming_chat_id}')
                activity('COMMAND_HANDLED', command=cmd, chat_id=incoming_chat_id)
            continue
        if not natural_message_allowed(text, msg, chat_type, incoming_chat_id, private_chat_id, group_chat_id, bot_username):
            continue
        activity('NATURAL_MESSAGE_RECEIVED', chat_id=incoming_chat_id, chat_type=chat_type, chars=len(text))
        reply = request_natural_reply(text, chat_type)
        if reply:
            send_message(token, incoming_chat_id, reply)
            had_activity = True
            activity('NATURAL_MESSAGE_HANDLED', chat_id=incoming_chat_id, chars=len(reply))
    save_state(state)
    return had_activity

def main() -> None:
    env = load_env(ENV_FILE)
    token = env.get('TELEGRAM_BOT_TOKEN')
    private_chat_id = str(env.get('TELEGRAM_CHAT_ID', ''))
    group_chat_id = str(env.get('TELEGRAM_GROUP_CHAT_ID') or env.get('GROUP_CHAT_ID') or '')
    bot_username = str(env.get('TELEGRAM_BOT_USERNAME') or BOT_USERNAME).lower()
    if not token or not private_chat_id:
        raise SystemExit('Missing KITT credentials')
    acquire_lock()
    atexit.register(release_lock)
    state = load_state()
    error_count = 0
    activity('LOOP_START', private_chat_id=private_chat_id, group_chat_id=group_chat_id)
    while True:
        try:
            had_activity = run_once(token, private_chat_id, group_chat_id, bot_username, state)
            if state.get('poll_error_active'):
                recovered_reason = state.get('last_poll_error_reason', '')
                activity('POLL_RECOVERED', reason=recovered_reason)
                log(f'KITT_BOT_POLL_RECOVERED reason={recovered_reason or "unknown"}')
                state['poll_error_active'] = False
                state['last_poll_error_reason'] = ''
                state['last_poll_error_detail'] = ''
                state['last_poll_error_at'] = ''
                state['last_poll_recovered_at'] = datetime.now().isoformat(timespec='seconds')
                save_state(state)
            error_count = 0
            update_heartbeat('kitt_bot.py', 'ok')
            activity('LOOP_TICK', had_activity=had_activity, last_update_id=state.get('last_update_id', 0))
        except Exception as exc:
            update_heartbeat('kitt_bot.py', 'degraded')
            reason = type(exc).__name__
            if not state.get('poll_error_active') or state.get('last_poll_error_reason') != reason:
                log(f'KITT_BOT_POLL_ERROR reason={reason}')
                activity('POLL_ERROR', reason=reason, detail=str(exc))
                state['poll_error_active'] = True
                state['last_poll_error_reason'] = reason
                state['last_poll_error_detail'] = str(exc)
                state['last_poll_error_at'] = datetime.now().isoformat(timespec='seconds')
            save_state(state)
            delay = ERROR_BACKOFF_SECONDS[min(error_count, len(ERROR_BACKOFF_SECONDS) - 1)]
            error_count += 1
            activity('BACKOFF_SLEEP', seconds=delay, error_count=error_count)
            time.sleep(delay)

if __name__ == '__main__':
    main()
