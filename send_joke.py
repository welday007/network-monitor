#!/usr/bin/env python3
from datetime import datetime
from html import escape, unescape
from pathlib import Path
import json
import random
import re
import urllib.parse
import urllib.request

BASE = Path('/home/jarvis/monitor')
TELEGRAM_ENV = BASE / 'telegram.env'
LLM_ENV = BASE / 'llm.env'
LOG = BASE / 'net-health.log'
ACTIVITY_LOG = BASE / 'activity.log'
DEFAULT_MODEL = 'openrouter/free'
CISA_URL = 'https://www.cisa.gov/news-events/cybersecurity-advisories'
SECURITY_TIPS = [
    'Patch browsers, Windows, and router firmware before chasing any new tool.',
    'If a login page feels urgent, check the domain before you type anything.',
    'Use unique passwords and MFA on email first; that is still the highest-value defense.',
    'If you do not need a port open on the router, close it and leave it closed.',
    'Backups matter more than panic: keep one copy offline or versioned.',
]
FALLBACK_BITS = {
    'joke': [
        'Today\'s joke: My firewall said it was broad-minded, then rejected everyone at the perimeter.',
        'Today\'s joke: I asked my password manager for emotional support. It generated something stronger than I felt.',
    ],
    'trivia': [
        'Today\'s trivia: Phishing borrowed its name from fishing because attackers cast wide and wait for one bite.',
        'Today\'s trivia: MFA still blocks an enormous number of account-takeover attempts after passwords leak.',
    ],
    'motivation': [
        'Today\'s motivation: Methodical security habits are still more useful than dramatic security speeches.',
        'Today\'s motivation: Clean updates, calm judgment, and reliable backups remain an excellent strategy.',
    ],
}

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

def log(message: str) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} {message}\n')

def activity(event: str, **fields) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    extras = ' '.join(f'{key}={json.dumps(value, ensure_ascii=True)}' for key, value in fields.items())
    with ACTIVITY_LOG.open('a', encoding='utf-8') as fh:
        fh.write(f'{ts} script=send_joke event={event}' + (f' {extras}' if extras else '') + '\n')

def send_telegram(token: str, chat_id: str, message: str) -> None:
    activity('SEND_ATTEMPT', chat_id=chat_id, chars=len(message))
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload, method='POST')
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()
    activity('SEND_OK', chat_id=chat_id, chars=len(message))

def fetch_cisa_news(limit: int = 3) -> list[dict]:
    req = urllib.request.Request(CISA_URL, headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req, timeout=25).read().decode('utf-8', errors='ignore')
    pattern = re.compile(r'href="(?P<href>/news-events/[^\"]+)"[^>]*>(?P<title>[^<]+)</a>', re.IGNORECASE)
    seen = set(); items = []
    skip_terms = ('read more', 'view', 'subscribe', 'all alerts', 'cybersecurity alerts & advisories')
    for match in pattern.finditer(html):
        href = match.group('href'); title = unescape(match.group('title')).strip(); lower = title.lower()
        if not title or len(title) < 8 or any(term in lower for term in skip_terms) or 'ics advisory' in lower or 'medical advisory' in lower or title in seen:
            continue
        seen.add(title); items.append({'title': title, 'url': f'https://www.cisa.gov{href}'})
        if len(items) >= limit:
            break
    return items

def request_openrouter(api_key: str, model: str, mode: str, tip: str, news_items: list[dict]) -> str:
    headlines = '\n'.join(f'- {item["title"]}' for item in news_items) if news_items else '- No major headline retrieved'
    prompt = (
        'Write a short Telegram morning cybersecurity briefing as a polished formal assistant with dry wit. '
        'Avoid direct imitation of any existing character. '
        'Open with two short lines labeled Briefing: and Observation:. '
        f'Then add one line for Today\'s {mode.title()}. '
        'Then add one line starting Security tip:. '
        'Then add a News: section with exactly three bullet lines, each paraphrasing why the headline matters in plain English. '
        'Keep it under 900 characters, plain text, elegant, and precise. '
        f'Use this security tip idea: {tip} '
        f'Use these headlines:\n{headlines}'
    )
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.75}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=40) as resp:
        body = json.loads(resp.read().decode('utf-8'))
    return body['choices'][0]['message']['content'].strip()

def fallback_brief(mode: str, tip: str, news_items: list[dict]) -> str:
    opener = 'Briefing: Good morning. Your systems appear willing to cooperate.\nObservation: That puts us ahead of most mornings already.'
    bit = random.choice(FALLBACK_BITS[mode])
    news_lines = [f'- {item["title"]}' for item in news_items[:3]]
    if not news_lines:
        news_lines.append('- No headline pull this morning, though the sensors remain attentive.')
    while len(news_lines) < 3:
        news_lines.append('- No additional headline retrieved this morning.')
    return '\n'.join([opener, '', bit, f'Security tip: {tip}', 'News:', *news_lines[:3]])

def build_html_message(body_text: str, news_items: list[dict]) -> str:
    safe_body = escape(body_text)
    if not news_items:
        return safe_body
    links = ['<b>Read more</b>:']
    for item in news_items[:3]:
        links.append(f'? <a href="{escape(item["url"], quote=True)}">{escape(item["title"])}</a>')
    return safe_body + '\n\n' + '\n'.join(links)

telegram = load_env(TELEGRAM_ENV); llm = load_env(LLM_ENV)
token = telegram.get('TELEGRAM_BOT_TOKEN'); chat_id = telegram.get('TELEGRAM_CHAT_ID'); api_key = llm.get('OPENROUTER_API_KEY'); model = llm.get('OPENROUTER_MODEL', DEFAULT_MODEL)
if not token or not chat_id:
    raise SystemExit('Missing Telegram credentials')
mode = random.choice(['joke', 'trivia', 'motivation']); tip = random.choice(SECURITY_TIPS)
activity('RUN_START', mode=mode)
try:
    news_items = fetch_cisa_news()
except Exception as exc:
    news_items = []; log(f'MORNING_NEWS_FALLBACK reason={type(exc).__name__}')
used_fallback = False
if api_key:
    try:
        body = request_openrouter(api_key, model, mode, tip, news_items)
    except Exception as exc:
        used_fallback = True; body = fallback_brief(mode, tip, news_items); log(f'MORNING_LLM_FALLBACK reason={type(exc).__name__}')
else:
    used_fallback = True; body = fallback_brief(mode, tip, news_items); log('MORNING_LLM_FALLBACK reason=missing_api_key')
message = build_html_message(body, news_items)
activity('MESSAGE_BUILT', mode=mode, used_fallback=used_fallback, links=bool(news_items), chars=len(message))
send_telegram(token, chat_id, message)
log(f'MORNING_SENT source={"fallback" if used_fallback else model} mode={mode} links={bool(news_items)}')
activity('RUN_COMPLETE', mode=mode, source=('fallback' if used_fallback else model))
