#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
from html import escape, unescape
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

BASE = Path('/home/jarvis/monitor'); TELEGRAM_ENV = BASE / 'telegram.env'; LLM_ENV = BASE / 'llm.env'; SEARCH_ENV = BASE / 'search.env'; LOG = BASE / 'net-health.log'; TARGETS = ['192.168.1.1', '1.1.1.1', '8.8.8.8']; DEFAULT_MODEL = 'openrouter/free'
ACTIVITY_LOG = BASE / 'activity.log'

def load_env(path: Path) -> dict:
    env = {}
    if not path.exists(): return env
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1); env[key.strip()] = value.strip()
    return env

def activity(event: str, **fields) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extras = ' '.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in fields.items())
    with ACTIVITY_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} script=send_status event={event}' + (f' {extras}' if extras else '') + '\n')

def send_telegram(token: str, chat_id: str, message: str) -> None:
    activity('SEND_ATTEMPT', chat_id=chat_id, chars=len(message))
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp: resp.read()
    activity('SEND_OK', chat_id=chat_id, chars=len(message))

def probe(host: str) -> tuple[str, str]:
    proc = subprocess.run(['fping', '-c1', '-t1500', host], capture_output=True, text=True)
    output = (proc.stdout or '') + (proc.stderr or '')
    match = re.search(r'([0-9]+(?:\.[0-9]+)?) ms', output)
    return ('UP' if proc.returncode == 0 else 'DOWN', match.group(1) if match else 'NA')

def today_traffic() -> tuple[str, str, str]:
    proc = subprocess.run(['vnstat', '--json'], capture_output=True, text=True)
    if proc.returncode != 0: return 'unknown', 'n/a', 'n/a'
    try:
        payload = json.loads(proc.stdout); iface = payload.get('interfaces', [{}])[0]; days = iface.get('traffic', {}).get('day', [])
        if not days: return iface.get('name', 'unknown'), 'n/a', 'n/a'
        day = days[-1]; return iface.get('name', 'unknown'), format_bytes(day.get('rx', 0)), format_bytes(day.get('tx', 0))
    except Exception:
        return 'unknown', 'n/a', 'n/a'

def format_bytes(value: int) -> str:
    units = ['KiB', 'MiB', 'GiB', 'TiB']; size = float(value); unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]: break
        size /= 1024
    return f'{size:.1f} {unit}'

def fetch_ai_headline(api_key: str):
    params = urllib.parse.urlencode({'engine': 'google_news', 'q': 'AI OR "artificial intelligence" OpenAI Google Microsoft Anthropic Nvidia', 'gl': 'us', 'hl': 'en', 'api_key': api_key, 'no_cache': 'true'})
    req = urllib.request.Request(f'https://serpapi.com/search.json?{params}')
    with urllib.request.urlopen(req, timeout=30) as resp: payload = json.loads(resp.read().decode('utf-8'))
    for item in payload.get('news_results', []):
        title = (item.get('title') or '').strip()
        if title: return {'title': unescape(title), 'source': item.get('source', {}).get('name', 'unknown source'), 'url': item.get('link', '')}
    return None

def request_dialog(api_key: str, model: str, headline: dict) -> str:
    prompt = (
        'Write a very short exchange in the style of a polished formal assistant with dry wit. '
        'Avoid direct imitation of any existing character. '
        'Exactly two lines only. First line starts with Briefing:, second line starts with Observation:. '
        'Use plain English, correct spelling, and elegant phrasing. Keep it under 45 words total. '
        f'Headline: {headline["title"]} Source: {headline["source"]}'
    )
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.55}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=35) as resp: body = json.loads(resp.read().decode('utf-8'))
    return clean_dialog(body['choices'][0]['message']['content'].strip())

def clean_dialog(text: str) -> str:
    text = re.sub(r'\s+', ' ', text.replace('\r', ' ')).strip()
    lines = []
    for marker in ('Briefing:', 'Observation:'):
        m = re.search(rf'{marker}\s*(.*?)(?=(Briefing:|Observation:|$))', text)
        if m: lines.append(f'{marker} {m.group(1).strip()}')
    return '\n'.join(lines) if len(lines) == 2 else text

def fallback_dialog(headline: dict) -> str:
    return 'Briefing: The AI industry remains theatrically busy.\nObservation: Today\'s headline concerns ' + headline['title'] + '.'

telegram = load_env(TELEGRAM_ENV); llm = load_env(LLM_ENV); search = load_env(SEARCH_ENV)
token = telegram.get('TELEGRAM_BOT_TOKEN'); chat_id = telegram.get('TELEGRAM_CHAT_ID')
if not token or not chat_id: raise SystemExit('Missing Telegram credentials')
activity('RUN_START', status_mode=os.environ.get('STATUS_MODE', 'auto'))
lines = []; down_hosts = 0
for host in TARGETS:
    status, rtt = probe(host)
    if status != 'UP': down_hosts += 1
    lines.append(f'{host}: {status} ({rtt} ms)')
iface, rx, tx = today_traffic(); sessions = subprocess.run(['who'], capture_output=True, text=True).stdout.splitlines(); session_count = len([s for s in sessions if s.strip()])
state = 'NOMINAL' if down_hosts == 0 else 'DEGRADED'; now = datetime.now().strftime('%Y-%m-%d %H:%M:%S'); mode = os.environ.get('STATUS_MODE', 'auto'); hour = datetime.now().hour; include_ai = mode == 'evening' or (mode == 'auto' and hour == 18); night_watch = mode == 'night' or (mode == 'auto' and hour == 20)
if night_watch:
    message_parts = ['<b>Jarvis Night Watch</b>', f'<b>State:</b> {escape(state)}', f'<b>Time:</b> {escape(now)}', '', '<b>Network</b>', *[escape(line) for line in lines], '', '<b>Quiet Summary</b>', escape(f'{rx} down and {tx} up on {iface} today.'), f'<b>Logged in now:</b> {session_count}']
else:
    message_parts = ['<b>Jarvis Status Update</b>', f'<b>State:</b> {escape(state)}', f'<b>Time:</b> {escape(now)}', '', '<b>Network</b>', *[escape(line) for line in lines], '', '<b>Internet Use Today</b>', escape(f'Downloaded {rx}, uploaded {tx} on {iface}.'), f'<b>People currently logged in:</b> {session_count}']
if include_ai:
    serp_key = search.get('SERPAPI_API_KEY'); llm_key = llm.get('OPENROUTER_API_KEY'); model = llm.get('OPENROUTER_MODEL', DEFAULT_MODEL); headline = None
    if serp_key:
        try: headline = fetch_ai_headline(serp_key)
        except Exception as exc:
            with LOG.open('a', encoding='utf-8') as fh: fh.write(f'{now} AI_HEADLINE_FALLBACK reason={type(exc).__name__}\n')
    if not headline: headline = {'title': 'AI companies are still racing to ship faster models', 'source': 'general news', 'url': ''}
    if llm_key:
        try: dialog = request_dialog(llm_key, model, headline)
        except Exception as exc:
            dialog = fallback_dialog(headline)
            with LOG.open('a', encoding='utf-8') as fh: fh.write(f'{now} AI_DIALOG_FALLBACK reason={type(exc).__name__}\n')
    else: dialog = fallback_dialog(headline)
    first, second = dialog.split('\n', 1) if '\n' in dialog else ('Briefing: Evening summary available.', f'Observation: {dialog}')
    message_parts.extend(['', '<b>AI Headline Of The Day</b>', escape(first), '', escape(second), ''])
    if headline.get('url'):
        message_parts.append(f'<b>Read more:</b> <a href="{escape(headline["url"], quote=True)}">{escape(headline["title"])}</a>')
        message_parts.append(f'<b>Source:</b> {escape(headline["source"])}')
    else:
        message_parts.append(escape(f'Headline: {headline["title"]} ({headline["source"]})'))
message = '\n'.join(message_parts)
activity('MESSAGE_BUILT', state=state, include_ai=include_ai, night_watch=night_watch, chars=len(message))
send_telegram(token, chat_id, message)
with LOG.open('a', encoding='utf-8') as fh: fh.write(f'{now} STATUS_UPDATE_SENT state={state} ai_section={include_ai} links={include_ai}\n')
activity('RUN_COMPLETE', state=state, include_ai=include_ai, night_watch=night_watch)
