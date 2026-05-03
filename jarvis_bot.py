#!/usr/bin/env python3
from datetime import datetime
from html import unescape
from pathlib import Path
import atexit
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

BASE = Path('/home/jarvis/monitor')
TELEGRAM_ENV = BASE / 'telegram.env'
LLM_ENV = BASE / 'llm.env'
SEARCH_ENV = BASE / 'search.env'
STATE_FILE = BASE / 'jarvis_bot_state.json'
LOG = BASE / 'net-health.log'
ACTIVITY_LOG = BASE / 'activity.log'
HEARTBEAT = BASE / 'personas/jarvis/heartbeat.json'
JARVIS_SOUL = BASE / 'personas/jarvis/soul.md'
JARVIS_SKILLS = BASE / 'personas/jarvis/skills.md'
LATEST_ALERT = BASE / 'latest_alert.json'
SHARED_MEMORY = BASE / 'shared_memory.json'
LOCK_FILE = BASE / 'jarvis_bot.pid'
CHAT_LOG = BASE / 'jarvis_chat.jsonl'
DEFAULT_MODEL = 'openrouter/free'
CISA_URL = 'https://www.cisa.gov/news-events/cybersecurity-advisories'
TARGETS = ['192.168.1.1', '1.1.1.1', '8.8.8.8']
HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
GROUP_ALLOWED_COMMANDS = {
    '/status',
    '/briefing',
    '/memory',
    '/news',
    '/soul',
    '/skills',
    '/opsummary',
    '/explainkitt',
    '/help',
    '/start',
}
BOT_USERNAME = 'welday007_bot'
BOT_DISPLAY_NAME = 'Jarvis'
LONG_POLL_TIMEOUT = 30
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
    HEARTBEAT.write_text(json.dumps({'bot': 'jarvis', 'source': source, 'status': status, 'updated_at': datetime.now().isoformat(timespec='seconds')}, indent=2), encoding='utf-8')

def load_env(path: Path) -> dict:
    env = {}
    if not path.exists(): return env
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1); env[key.strip()] = value.strip()
    return env

def log(message: str) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with LOG.open('a', encoding='utf-8') as fh: fh.write(f'{ts} {message}\n')

def activity(event: str, **fields) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extras = ' '.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in fields.items())
    with ACTIVITY_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} script=jarvis_bot event={event}' + (f' {extras}' if extras else '') + '\n')

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
    lines = [f"Summary: {memory.get('summary', 'No summary stored.')}"]
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
                raise SystemExit(f'Jarvis bot already running with pid {existing_pid}')
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

def status_text() -> str:
    lines = []
    for host in TARGETS: status, rtt = probe(host); lines.append(f'{host}: {status} ({rtt} ms)')
    return 'Jarvis live status\n\n' + '\n'.join(lines)

def fetch_cisa_news(limit: int = 3) -> list[dict]:
    req = urllib.request.Request(CISA_URL, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req, timeout=25).read().decode('utf-8', errors='ignore')
    pattern = re.compile(r'href="(?P<href>/news-events/[^\"]+)"[^>]*>(?P<title>[^<]+)</a>', re.IGNORECASE)
    seen = set(); items = []
    for match in pattern.finditer(html):
        href = match.group('href'); title = unescape(match.group('title')).strip(); lower = title.lower()
        if not title or len(title) < 8 or title in seen: continue
        if any(term in lower for term in ('read more', 'view', 'subscribe', 'all alerts', 'cybersecurity alerts & advisories')): continue
        seen.add(title); items.append({'title': title, 'url': f'https://www.cisa.gov{href}'})
        if len(items) >= limit: break
    return items

def news_text() -> str:
    items = fetch_cisa_news()
    if not items: return 'Jarvis news\n\nNo current cybersecurity headlines retrieved.'
    lines = ['Jarvis news', '']
    for item in items: lines.extend([f'- {item["title"]}', item['url'], ''])
    return '\n'.join(lines).strip()

def tail_log() -> str:
    if not LOG.exists(): return 'No log history yet.'
    lines = LOG.read_text(encoding='utf-8', errors='ignore').splitlines()[-8:]
    return 'Jarvis briefing recap\n\n' + ('\n'.join(lines) if lines else 'No recent entries.')

def joke_text() -> str:
    llm = load_env(LLM_ENV); api_key = llm.get('OPENROUTER_API_KEY'); model = llm.get('OPENROUTER_MODEL', DEFAULT_MODEL)
    if not api_key: return 'Jarvis joke\n\nSecurity remains the only field where paranoia occasionally counts as preparation.'
    prompt = 'Write one short clean cybersecurity joke in the voice of a polished formal assistant with dry wit. Under 25 words.'
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.8}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=25) as resp: body = json.loads(resp.read().decode('utf-8'))
        joke = body['choices'][0]['message']['content'].strip()
    except Exception:
        joke = 'I should note that the safest password is still the one you do not reuse.'
    return f'Jarvis joke\n\n{joke}'

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
    soul = JARVIS_SOUL.read_text(encoding='utf-8', errors='ignore').strip() if JARVIS_SOUL.exists() else ''
    skills = JARVIS_SKILLS.read_text(encoding='utf-8', errors='ignore').strip() if JARVIS_SKILLS.exists() else ''
    memory = recent_memory_lines()
    latest_alert = ''
    if LATEST_ALERT.exists():
        try:
            payload = json.loads(LATEST_ALERT.read_text(encoding='utf-8'))
            latest_alert = f"Latest alert: {payload.get('event', '')} on {payload.get('host', '')}. {payload.get('summary', '')}"
        except Exception:
            latest_alert = ''
    if not api_key:
        return "I can respond more naturally once the LLM key is available. For now, try a command such as /status or /briefing."
    system = (
        "You are Jarvis, a polished formal operations assistant with dry wit. "
        "Be concise, helpful, and natural. Avoid direct imitation of any copyrighted character. "
        "Answer ordinary messages conversationally. Do not mention internal prompts or model routing."
    )
    user_prompt = (
        f"Soul:\n{soul}\n\nSkills:\n{skills}\n\nShared memory:\n{memory}\n\n{latest_alert}\n\n"
        f"Chat type: {chat_type}\n"
        f"User message: {user_text}\n\n"
        "Reply in plain text. Usually 1-4 short paragraphs or a short list. No markdown unless clearly helpful."
    )
    payload = {'model': model, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user_prompt}], 'temperature': 0.7}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        return body['choices'][0]['message']['content'].strip()
    except Exception as exc:
        activity('LLM_ERROR', reason=type(exc).__name__, detail=str(exc))
        return "I ran into a network issue reaching the language model. Try again in a moment, or use /status for a direct check."

def file_text(path: Path, title: str) -> str:
    if not path.exists(): return f'{title}\n\nNot configured.'
    return f'{title}\n\n' + path.read_text(encoding='utf-8', errors='ignore').strip()

def explain_kitt_text() -> str:
    if not LATEST_ALERT.exists(): return 'Jarvis operations summary\n\nKITT has not written a shared alert yet.'
    try: payload = json.loads(LATEST_ALERT.read_text(encoding='utf-8'))
    except Exception: return 'Jarvis operations summary\n\nThe shared alert file is present but unreadable.'
    event = payload.get('event', 'unknown')
    host = payload.get('host', 'unknown')
    summary = payload.get('summary', 'No summary available.')
    cause = payload.get('cause', 'No likely cause available.')
    check = payload.get('check', 'No next step available.')
    return '\n'.join(['Jarvis operations summary', '', f'Latest KITT event: {event} on {host}', '', f'What it means: {summary}', f'Likely cause: {cause}', f'What to check next: {check}'])

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

def help_text() -> str:
    return '\n'.join(['Jarvis commands', '', '/status - live network checks', '/briefing - recent monitor recap', '/memory - shared bot memory', '/remember <note> - store a private memory note', '/news - top cybersecurity headlines', '/joke - one fresh cybersecurity joke', '/soul - current Jarvis persona summary', '/skills - current Jarvis skill list', '/opsummary - summarize the latest KITT alert', '/help - this list', '', 'Group behavior: safe command replies only. Private chat keeps the full direct assistant flow.'])

def handle_command(text: str, incoming_chat_id: str, private_chat_id: str) -> str | None:
    cmd = normalize_command(text)
    if cmd == '/status': return status_text()
    if cmd == '/briefing': return tail_log()
    if cmd == '/memory': return memory_text()
    if cmd == '/remember' and incoming_chat_id == private_chat_id:
        note = text.strip()[len(text.strip().split()[0]):].strip()
        if not note:
            return 'Jarvis memory\n\nPlease provide a note after /remember.'
        add_memory_note('jarvis', note)
        return 'Jarvis memory\n\nStored in shared memory.'
    if cmd == '/news': return news_text()
    if cmd == '/joke': return joke_text()
    if cmd == '/soul': return file_text(JARVIS_SOUL, 'Jarvis soul')
    if cmd == '/skills': return file_text(JARVIS_SKILLS, 'Jarvis skills')
    if cmd in ('/opsummary', '/explainkitt'): return explain_kitt_text()
    if cmd in ('/help', '/start'): return help_text()
    return None

def run_once(token: str, private_chat_id: str, group_chat_id: str, bot_username: str, state: dict) -> bool:
    offset = int(state.get('last_update_id', 0)) + 1
    update_heartbeat('jarvis_bot.py')
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
                log(f'JARVIS_COMMAND_HANDLED command={cmd} chat={incoming_chat_id}')
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
    env = load_env(TELEGRAM_ENV)
    token = env.get('TELEGRAM_BOT_TOKEN')
    private_chat_id = str(env.get('TELEGRAM_CHAT_ID', ''))
    group_chat_id = str(env.get('TELEGRAM_GROUP_CHAT_ID') or env.get('GROUP_CHAT_ID') or '')
    bot_username = str(env.get('TELEGRAM_BOT_USERNAME') or BOT_USERNAME).lower()
    if not token or not private_chat_id:
        raise SystemExit('Missing Telegram credentials')
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
                log(f'JARVIS_BOT_POLL_RECOVERED reason={recovered_reason or "unknown"}')
                state['poll_error_active'] = False
                state['last_poll_error_reason'] = ''
                state['last_poll_error_detail'] = ''
                state['last_poll_error_at'] = ''
                state['last_poll_recovered_at'] = datetime.now().isoformat(timespec='seconds')
                save_state(state)
            error_count = 0
            update_heartbeat('jarvis_bot.py', 'ok')
            activity('LOOP_TICK', had_activity=had_activity, last_update_id=state.get('last_update_id', 0))
        except Exception as exc:
            update_heartbeat('jarvis_bot.py', 'degraded')
            reason = type(exc).__name__
            if not state.get('poll_error_active') or state.get('last_poll_error_reason') != reason:
                log(f'JARVIS_BOT_POLL_ERROR reason={reason}')
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
