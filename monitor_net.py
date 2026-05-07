#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import json
import re
import subprocess
import statistics
import urllib.parse
import urllib.request
from http_retry import read_json_retry, urlopen_retry

BASE = Path('/home/jarvis/monitor')
LOG = BASE / 'net-health.log'
ACTIVITY_LOG = BASE / 'activity.log'
STATE_DIR = BASE / 'state'
ALERT_STATE = BASE / 'alert_state.json'
LATEST_ALERT = BASE / 'latest_alert.json'
ENV_FILE = BASE / 'kitt.env'
JARVIS_ENV = BASE / 'telegram.env'
LLM_ENV = BASE / 'llm.env'
MONITOR_HEARTBEAT = BASE / 'personas/monitor_net/heartbeat.json'
COORDINATION_STATE = BASE / 'coordination_state.json'
SHARED_MEMORY = BASE / 'shared_memory.json'
LATENCY_HISTORY = BASE / 'latency_history.json'
TARGETS = ['192.168.1.1', '1.1.1.1', '8.8.8.8']
LATENCY_SAMPLE_LIMIT = 240
LATENCY_BASELINE_INTERVAL_MINUTES = 360
LATENCY_BASELINE_LIMIT = 120
LATENCY_BREACH_LIMIT = 240
WEEKLY_JITTER_DAY = 0
MIN_SAMPLES_FOR_STATS = 12
SIGMA_THRESHOLD = 2.0
CONSECUTIVE_ANOMALIES = 2
KITT_SEVERE_EVENTS = {'ROUTER_DOWN', 'INTERNET_OUTAGE'}
DEFAULT_MODEL = 'openrouter/free'
JARVIS_FOLLOWUP_COOLDOWN_SECONDS = 30 * 60
STATE_DIR.mkdir(parents=True, exist_ok=True)
MONITOR_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)

def update_heartbeat(source: str) -> None:
    MONITOR_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    MONITOR_HEARTBEAT.write_text(json.dumps({'bot': 'monitor_net', 'source': source, 'status': 'ok', 'updated_at': datetime.now().isoformat(timespec='seconds')}, indent=2), encoding='utf-8')

def write_latest_alert(payload: dict) -> None:
    LATEST_ALERT.write_text(json.dumps(payload, indent=2), encoding='utf-8')

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
        fh.write(f'{ts} script=monitor_net event={event}' + (f' {extras}' if extras else '') + '\n')

def send_telegram(token: str, chat_id: str, message: str) -> None:
    activity('SEND_ATTEMPT', chat_id=chat_id, chars=len(message))
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': message}).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=payload,
        method='POST',
        headers={'User-Agent': 'Mozilla/5.0', 'Connection': 'close'},
    )
    with urlopen_retry(req, timeout=15):
        pass
    activity('SEND_OK', chat_id=chat_id, chars=len(message))

def configured_destinations(*chat_ids: str | None) -> list[str]:
    ordered = []
    for chat_id in chat_ids:
        if chat_id and chat_id not in ordered:
            ordered.append(chat_id)
    return ordered

def load_coordination_state() -> dict:
    if COORDINATION_STATE.exists():
        try:
            payload = json.loads(COORDINATION_STATE.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                payload.setdefault('followups', {})
                return payload
        except Exception:
            pass
    return {'followups': {}}

def save_coordination_state(state: dict) -> None:
    COORDINATION_STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')

def load_shared_memory() -> dict:
    if SHARED_MEMORY.exists():
        try:
            payload = json.loads(SHARED_MEMORY.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                payload.setdefault('summary', 'No shared memory stored yet.')
                payload.setdefault('notes', [])
                payload.setdefault('recent_events', [])
                payload.setdefault('updated_at', '')
                payload.setdefault('updated_by', '')
                return payload
        except Exception:
            pass
    return {'updated_at': '', 'updated_by': '', 'summary': 'No shared memory stored yet.', 'notes': [], 'recent_events': []}

def save_shared_memory(memory: dict) -> None:
    memory['updated_at'] = datetime.now().isoformat(timespec='seconds')
    SHARED_MEMORY.write_text(json.dumps(memory, indent=2), encoding='utf-8')

def remember_event(source: str, payload: dict) -> None:
    memory = load_shared_memory()
    events = [item for item in memory.get('recent_events', []) if isinstance(item, dict)]
    events.append({
        'time': payload.get('time', datetime.now().isoformat(timespec='seconds')),
        'source': source,
        'event': payload.get('event', ''),
        'host': payload.get('host', ''),
        'summary': payload.get('summary', ''),
    })
    memory['recent_events'] = events[-12:]
    memory['updated_by'] = source
    memory['summary'] = payload.get('summary', memory.get('summary', ''))
    save_shared_memory(memory)

def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def alert_key(payload: dict) -> str:
    return '|'.join([str(payload.get('event', '')), str(payload.get('host', '')), str(payload.get('status', ''))])

def load_alert_state() -> dict:
    if ALERT_STATE.exists():
        try:
            payload = json.loads(ALERT_STATE.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                payload.setdefault('anomaly_counts', {})
                payload.setdefault('anomaly_active', {})
                payload.setdefault('weekly_latency_summary', {})
                payload.setdefault('internet_down_active', False)
                payload.setdefault('router_down_active', False)
                return payload
        except Exception: pass
    return {
        'anomaly_counts': {},
        'anomaly_active': {},
        'weekly_latency_summary': {},
        'internet_down_active': False,
        'router_down_active': False,
    }

def save_alert_state(state: dict) -> None:
    ALERT_STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')

def load_latency_history() -> dict:
    if LATENCY_HISTORY.exists():
        try:
            payload = json.loads(LATENCY_HISTORY.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                normalized = {}
                for host, samples in payload.items():
                    normalized[host] = normalize_latency_samples(samples if isinstance(samples, list) else [])
                return normalized
        except Exception:
            pass
    return {host: [] for host in TARGETS}

def save_latency_history(history: dict) -> None:
    LATENCY_HISTORY.write_text(json.dumps(history, indent=2), encoding='utf-8')

def normalize_latency_samples(samples: list) -> list[dict]:
    normalized = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        time = str(item.get('time', '')).strip()
        status = str(item.get('status', 'UNKNOWN')).upper()
        sample_type = str(item.get('type', 'recent')).lower()
        rtt = item.get('rtt')
        if not time:
            continue
        if rtt is not None and not isinstance(rtt, (int, float)):
            try:
                rtt = float(rtt)
            except Exception:
                rtt = None
        normalized.append({'time': time, 'status': status, 'rtt': rtt, 'type': sample_type})
    return normalized

def classify_latency_sample(status: str, rtt: float | None, mean: float | None, stdev: float | None) -> str:
    if status != 'UP':
        return 'breach'
    if rtt is None or mean is None or stdev in (None, 0.0):
        return 'recent'
    return 'breach' if rtt >= (mean + (SIGMA_THRESHOLD * stdev)) else 'recent'

def append_latency_sample(history: dict, host: str, status: str, rtt: float | None) -> None:
    samples = normalize_latency_samples(history.get(host, []))
    usable = [float(item['rtt']) for item in samples if item.get('status') == 'UP' and isinstance(item.get('rtt'), (int, float))]
    recent = [item for item in samples if item.get('type') == 'recent']
    baseline = [item for item in samples if item.get('type') == 'baseline']
    breaches = [item for item in samples if item.get('type') == 'breach']
    mean = statistics.fmean(usable[-60:]) if len(usable[-60:]) >= MIN_SAMPLES_FOR_STATS else None
    stdev = statistics.stdev(usable[-60:]) if len(usable[-60:]) >= 2 else None
    now = datetime.now()
    sample_type = classify_latency_sample(status, rtt, mean, stdev)
    sample = {
        'time': now.isoformat(timespec='seconds'),
        'status': status,
        'rtt': rtt,
        'type': sample_type,
    }
    recent.append(sample)
    recent = recent[-LATENCY_SAMPLE_LIMIT:]
    if sample_type == 'breach':
        breaches.append(sample)
    last_baseline = parse_time(str(baseline[-1].get('time', ''))) if baseline else None
    if not last_baseline or (now - last_baseline).total_seconds() >= (LATENCY_BASELINE_INTERVAL_MINUTES * 60):
        baseline.append({'time': sample['time'], 'status': status, 'rtt': rtt, 'type': 'baseline'})
    history[host] = recent[-LATENCY_SAMPLE_LIMIT:] + baseline[-LATENCY_BASELINE_LIMIT:] + breaches[-LATENCY_BREACH_LIMIT:]

def latency_stats(history: dict, host: str) -> dict:
    samples = [item for item in history.get(host, []) if isinstance(item, dict)]
    usable = [float(item['rtt']) for item in samples if item.get('status') == 'UP' and isinstance(item.get('rtt'), (int, float))]
    recent = usable[-60:]
    if len(recent) < MIN_SAMPLES_FOR_STATS:
        return {'count': len(recent), 'mean': None, 'stdev': None}
    mean = statistics.fmean(recent)
    stdev = statistics.stdev(recent) if len(recent) >= 2 else 0.0
    return {'count': len(recent), 'mean': mean, 'stdev': stdev}

def severe_public_outage(probes: dict) -> bool:
    router = probes.get('192.168.1.1', ('UNKNOWN', None))[0]
    cloudflare = probes.get('1.1.1.1', ('UNKNOWN', None))[0]
    google = probes.get('8.8.8.8', ('UNKNOWN', None))[0]
    return router == 'UP' and cloudflare == 'DOWN' and google == 'DOWN'

def probe(host: str) -> tuple[str, float | None]:
    proc = subprocess.run(['fping', '-c1', '-t1500', host], capture_output=True, text=True)
    output = (proc.stdout or '') + (proc.stderr or '')
    status = 'UP' if proc.returncode == 0 else 'DOWN'
    match = re.search(r'([0-9]+(?:\.[0-9]+)?) ms', output)
    rtt = float(match.group(1)) if match else None
    return status, rtt

def summarize_context(host: str, event: str, rtt: float | None, probes: dict) -> tuple[str, str, str]:
    router = probes.get('192.168.1.1', ('UNKNOWN', None))[0]
    cloudflare = probes.get('1.1.1.1', ('UNKNOWN', None))[0]
    google = probes.get('8.8.8.8', ('UNKNOWN', None))[0]
    if event == 'INTERNET_OUTAGE':
        return ('The house still sees the router, but the outside internet is down.', 'That usually points upstream, not to your Dell or Wi-Fi.', 'If this keeps happening, reboot the router once and wait a minute before checking again.')
    if event == 'INTERNET_RECOVERY':
        return ('The outside connection came back while the router stayed healthy.', 'The problem likely cleared on its own.', 'No action is needed right now.')
    if event == 'ROUTER_DOWN':
        return ('The Dell lost the router.', 'This is usually a local problem, or the router just needs a reboot.', 'Reboot the router first. If that does not help, reboot the Dell.')
    if event == 'ROUTER_RECOVERY':
        return ('The Dell can reach the router again.', 'The local connection recovered.', 'No action is needed unless it drops again.')
    if event == 'LATENCY_ANOMALY':
        return (f'One connection is running slower than usual: {host}.', 'It looks like a temporary slowdown rather than a full outage.', 'No immediate action needed. If it keeps showing up, reboot the router once.')
    if event == 'LATENCY_NORMALIZED':
        return (f'The slow connection to {host} has settled back down.', 'The temporary slowdown appears to be over.', 'No action is needed.')
    if event == 'STATE_CHANGE' and host != '192.168.1.1' and router == 'UP' and cloudflare == 'DOWN' and google == 'DOWN':
        return ('The router is fine, but the outside internet is not responding.', 'That points upstream, not to the Dell.', 'Reboot the router once if this has not cleared after a few minutes.')
    if event == 'STATE_CHANGE' and host == '192.168.1.1' and router == 'DOWN':
        return ('The Dell cannot reach the router.', 'This is usually a local connection problem.', 'Reboot the router first. If that fails, reboot the Dell.')
    if event == 'HIGH_LATENCY':
        return (f'{host} is running slower than normal.', 'This is likely a temporary slowdown rather than a hard failure.', 'No immediate action needed. Reboot the router only if it starts affecting everything.')
    if event == 'LATENCY_RECOVERY':
        return (f'{host} is back to normal.', 'The earlier slowdown appears to have cleared.', 'No action is needed.')
    return (f'The network changed on {host}.', 'This is probably temporary.', 'If the problem sticks around, reboot the router first and then the Dell.')

def request_kitt_message(api_key: str, model: str, event: str, host: str, rtt: float | None, summary: str, cause: str, check: str) -> str:
    prompt = ('Write a short Telegram alert for Kevin in natural English. Be calm, direct, and useful. Avoid technical jargon unless it helps a decision. No markdown, no emojis, under 85 words. Use 3 short lines: what is happening, what it probably means, and what Kevin should do next. When possible, recommend only practical actions: wait and recheck, reboot the router, or reboot the Dell. Address Kevin once if natural. ' f'Event: {event}. Host: {host}. RTT: {rtt if rtt is not None else "NA"}. ' f'Summary: {summary} Cause: {cause} Check: {check}')
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.45}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    body = read_json_retry(req, timeout=30)
    return body['choices'][0]['message']['content'].strip()

def fallback_kitt_message(host: str, summary: str, cause: str, check: str) -> str:
    return '\n'.join([f'KITT: Kevin, the connection involving {host} looks off.', f'What it probably means: {cause}', f'What to do: {check}'])

def request_jarvis_followup(api_key: str, model: str, payload: dict) -> str:
    prompt = (
        'Write one short Telegram follow-up for Kevin in plain language. Be polished but practical, with a little dry wit if it fits. '
        'Avoid direct imitation of any existing character. Under 75 words. '
        'Acknowledge KITT already raised the alert, explain what it means in simple terms, and suggest the one or two actions Kevin can actually take. '
        f"Event: {payload.get('event')}. Host: {payload.get('host')}. Status: {payload.get('status')}. RTT: {payload.get('rtt')}. "
        f"Summary: {payload.get('summary')} Cause: {payload.get('cause')} Check: {payload.get('check')}"
    )
    body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.35}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(body).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    response = read_json_retry(req, timeout=30)
    return response['choices'][0]['message']['content'].strip()

def fallback_jarvis_followup(payload: dict) -> str:
    return '\n'.join([
        'Jarvis note.',
        payload.get('summary', 'Something in the network changed.'),
        f"Best next step: {payload.get('check', 'Reboot the router first, then the Dell if needed.')}",
    ])

def latency_week_window_samples(samples: list[dict], end: datetime, days: int) -> list[dict]:
    start = end - timedelta(days=days)
    scoped = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        ts = parse_time(str(item.get('time', '')))
        if ts and start <= ts < end:
            scoped.append(item)
    return scoped

def summarize_latency_week(history: dict) -> str | None:
    now = datetime.now()
    if now.weekday() != WEEKLY_JITTER_DAY:
        return None
    week_key = now.strftime('%Y-%m-%d')
    last_sent = state.get('weekly_latency_summary', {}).get('sent_for_week')
    if last_sent == week_key:
        return None
    hosts = [host for host in TARGETS if host != '192.168.1.1']
    pieces = []
    week_quality = []
    for host in hosts:
        samples = normalize_latency_samples(history.get(host, []))
        current_week = latency_week_window_samples(samples, now, 7)
        previous_week = latency_week_window_samples(samples, now - timedelta(days=7), 7)
        current_values = [float(item['rtt']) for item in current_week if item.get('status') == 'UP' and isinstance(item.get('rtt'), (int, float))]
        previous_values = [float(item['rtt']) for item in previous_week if item.get('status') == 'UP' and isinstance(item.get('rtt'), (int, float))]
        current_avg = statistics.fmean(current_values) if current_values else None
        previous_avg = statistics.fmean(previous_values) if previous_values else None
        current_breaches = sum(1 for item in current_week if item.get('type') == 'breach' or item.get('status') != 'UP')
        previous_breaches = sum(1 for item in previous_week if item.get('type') == 'breach' or item.get('status') != 'UP')
        if current_avg is None and previous_avg is None:
            continue
        if current_avg is None:
            trend = 'worse'
        elif previous_avg is None:
            trend = 'no comparison'
        else:
            delta = current_avg - previous_avg
            if abs(delta) < 1.0:
                trend = 'about the same'
            elif delta < 0:
                trend = 'better'
            else:
                trend = 'worse'
        better_or_worse = 'better' if trend == 'better' else 'worse' if trend == 'worse' else 'about the same'
        pieces.append(f'{host}: {better_or_worse} than last week')
        week_quality.append((host, current_avg, previous_avg, current_breaches, previous_breaches, trend))
    if not pieces:
        return None
    overall = []
    better_count = sum(1 for _, _, _, _, _, trend in week_quality if trend == 'better')
    worse_count = sum(1 for _, _, _, _, _, trend in week_quality if trend == 'worse')
    same_count = sum(1 for _, _, _, _, _, trend in week_quality if trend == 'about the same')
    if better_count > worse_count:
        overall.append('Overall: slightly better than last week.')
    elif worse_count > better_count:
        overall.append('Overall: slightly worse than last week.')
    else:
        overall.append('Overall: about the same as last week.')
    if better_count or worse_count or same_count:
        overall.append(f'Trend: {better_count} better, {worse_count} worse, {same_count} about the same.')
    details = []
    for host, current_avg, previous_avg, current_breaches, previous_breaches, trend in week_quality:
        if current_avg is None or previous_avg is None:
            continue
        delta = current_avg - previous_avg
        direction = 'improved' if delta < 0 else 'got worse' if delta > 0 else 'stayed flat'
        details.append(f'{host}: {direction} by {abs(delta):.1f} ms, breaches {current_breaches} vs {previous_breaches}.')
    text = '\n'.join([
        'Weekly connection summary for Kevin.',
        *overall,
        *details,
        'Practical read: if the whole house feels off, reboot the router. If only the Dell feels off, reboot the Dell.',
    ])
    state['weekly_latency_summary']['sent_for_week'] = week_key
    return text

def should_send_kitt(event: str) -> bool:
    return event in KITT_SEVERE_EVENTS

def maybe_send_kitt_alert(event: str, payload: dict, token: str | None, private_chat_id: str | None, group_chat_id: str | None, api_key: str | None, model: str) -> None:
    if not token or not should_send_kitt(event):
        return
    try:
        text = request_kitt_message(api_key, model, event, str(payload.get('host', '')), payload.get('rtt'), str(payload.get('summary', '')), str(payload.get('cause', '')), str(payload.get('check', ''))) if api_key else fallback_kitt_message(str(payload.get('host', '')), str(payload.get('summary', '')), str(payload.get('cause', '')), str(payload.get('check', '')))
    except Exception as exc:
        text = fallback_kitt_message(str(payload.get('host', '')), str(payload.get('summary', '')), str(payload.get('cause', '')), str(payload.get('check', '')))
        log(f'KITT_LLM_FALLBACK reason={type(exc).__name__}')
    for destination in configured_destinations(private_chat_id, group_chat_id):
        send_telegram(token, destination, text)

def maybe_send_jarvis_group_followup(jarvis_token: str | None, jarvis_group_chat_id: str | None, api_key: str | None, model: str, payload: dict, coordination_level: int, coordination_state: dict) -> None:
    if coordination_level < 2 or not jarvis_token or not jarvis_group_chat_id:
        return
    key = alert_key(payload)
    followups = coordination_state.setdefault('followups', {})
    if not isinstance(followups, dict):
        followups = {}
        coordination_state['followups'] = followups
    last_sent = followups.get(key, {})
    if isinstance(last_sent, dict):
        sent_at = parse_time(str(last_sent.get('sent_at', '')))
    else:
        sent_at = parse_time(str(last_sent))
    if sent_at and (datetime.now() - sent_at).total_seconds() < JARVIS_FOLLOWUP_COOLDOWN_SECONDS:
        return
    try:
        message = request_jarvis_followup(api_key, model, payload) if api_key else fallback_jarvis_followup(payload)
    except Exception as exc:
        message = fallback_jarvis_followup(payload)
        log(f'JARVIS_LLM_FALLBACK reason={type(exc).__name__}')
    send_telegram(jarvis_token, jarvis_group_chat_id, message)
    followups[key] = {'sent_at': datetime.now().isoformat(timespec='seconds')}
    log(f'JARVIS_GROUP_FOLLOWUP_SENT event={payload.get("event")} host={payload.get("host")}')

alert_env = load_env(ENV_FILE)
jarvis_env = load_env(JARVIS_ENV)
llm_env = load_env(LLM_ENV)
state = load_alert_state()
latency_history = load_latency_history()
coordination_state = load_coordination_state()
token = alert_env.get('TELEGRAM_BOT_TOKEN')
private_chat_id = alert_env.get('TELEGRAM_CHAT_ID')
group_chat_id = alert_env.get('TELEGRAM_GROUP_CHAT_ID') or alert_env.get('GROUP_CHAT_ID')
jarvis_token = jarvis_env.get('TELEGRAM_BOT_TOKEN')
jarvis_group_chat_id = jarvis_env.get('TELEGRAM_GROUP_CHAT_ID') or jarvis_env.get('GROUP_CHAT_ID')
coordination_level = int(alert_env.get('BOT_COORDINATION_LEVEL') or jarvis_env.get('BOT_COORDINATION_LEVEL') or '2')
api_key = llm_env.get('OPENROUTER_API_KEY')
model = llm_env.get('OPENROUTER_MODEL', DEFAULT_MODEL)
probes = {host: probe(host) for host in TARGETS}
update_heartbeat('monitor_net.py')
activity('RUN_START', targets=TARGETS)
for host, (status, rtt) in probes.items():
    append_latency_sample(latency_history, host, status, rtt)
    state_file = STATE_DIR / host
    prev = state_file.read_text().strip() if state_file.exists() else 'UNKNOWN'
    state_file.write_text(status)
    if status != prev:
        summary, cause, check = summarize_context(host, 'STATE_CHANGE', rtt, probes)
        payload = {'time': datetime.now().isoformat(timespec='seconds'), 'event': 'STATE_CHANGE', 'host': host, 'status': status, 'rtt': rtt, 'summary': summary, 'cause': cause, 'check': check, 'probes': {k: {'status': v[0], 'rtt': v[1]} for k, v in probes.items()}}
        write_latest_alert(payload)
        remember_event('monitor', payload)
        log(f'STATE_CHANGE host={host} status={status} rtt={rtt if rtt is not None else "NA"}')
        activity('STATE_CHANGE', host=host, status=status, rtt=rtt, summary=summary)

router_down = probes['192.168.1.1'][0] == 'DOWN'
if router_down and not state.get('router_down_active', False):
    host, status, rtt = '192.168.1.1', probes['192.168.1.1'][0], probes['192.168.1.1'][1]
    summary, cause, check = summarize_context(host, 'ROUTER_DOWN', rtt, probes)
    payload = {'time': datetime.now().isoformat(timespec='seconds'), 'event': 'ROUTER_DOWN', 'host': host, 'status': status, 'rtt': rtt, 'summary': summary, 'cause': cause, 'check': check, 'probes': {k: {'status': v[0], 'rtt': v[1]} for k, v in probes.items()}}
    write_latest_alert(payload); remember_event('monitor', payload); log('ROUTER_DOWN'); activity('ROUTER_DOWN', summary=summary)
    maybe_send_kitt_alert('ROUTER_DOWN', payload, token, private_chat_id, group_chat_id, api_key, model)
    maybe_send_jarvis_group_followup(jarvis_token, jarvis_group_chat_id, api_key, model, payload, coordination_level, coordination_state)
state['router_down_active'] = router_down

if not router_down and state.get('router_down_active') and probes['192.168.1.1'][0] == 'UP':
    pass

internet_down = severe_public_outage(probes)
if internet_down and not state.get('internet_down_active', False):
    host, status, rtt = 'wan', 'DOWN', None
    summary, cause, check = summarize_context(host, 'INTERNET_OUTAGE', rtt, probes)
    payload = {'time': datetime.now().isoformat(timespec='seconds'), 'event': 'INTERNET_OUTAGE', 'host': host, 'status': status, 'rtt': rtt, 'summary': summary, 'cause': cause, 'check': check, 'probes': {k: {'status': v[0], 'rtt': v[1]} for k, v in probes.items()}}
    write_latest_alert(payload); remember_event('monitor', payload); log('INTERNET_OUTAGE'); activity('INTERNET_OUTAGE', summary=summary)
    maybe_send_kitt_alert('INTERNET_OUTAGE', payload, token, private_chat_id, group_chat_id, api_key, model)
    maybe_send_jarvis_group_followup(jarvis_token, jarvis_group_chat_id, api_key, model, payload, coordination_level, coordination_state)
state['internet_down_active'] = internet_down

for host, (status, rtt) in probes.items():
    if host == '192.168.1.1':
        continue
    stats = latency_stats(latency_history, host)
    anomaly_counts = state.setdefault('anomaly_counts', {})
    anomaly_active = state.setdefault('anomaly_active', {})
    count = int(anomaly_counts.get(host, 0))
    active = bool(anomaly_active.get(host, False))
    if status == 'UP' and rtt is not None and stats['mean'] is not None and stats['stdev'] not in (None, 0.0):
        z_score = (rtt - stats['mean']) / stats['stdev']
        if z_score >= SIGMA_THRESHOLD:
            count += 1
            anomaly_counts[host] = count
            if count >= CONSECUTIVE_ANOMALIES and not active:
                anomaly_active[host] = True
        else:
            anomaly_counts[host] = 0
            anomaly_active[host] = False
    else:
        anomaly_counts[host] = 0
save_latency_history(latency_history)
weekly_summary = summarize_latency_week(latency_history)
if weekly_summary and token and private_chat_id:
    send_telegram(token, private_chat_id, weekly_summary)
    payload = {'time': datetime.now().isoformat(timespec='seconds'), 'event': 'WEEKLY_LATENCY_SUMMARY', 'host': 'latency', 'status': 'INFO', 'summary': weekly_summary, 'cause': 'Weekly comparison of latency history.', 'check': 'No action needed unless the trend keeps getting worse.', 'probes': {k: {'status': v[0], 'rtt': v[1]} for k, v in probes.items()}}
    write_latest_alert(payload)
    remember_event('monitor', payload)
    activity('WEEKLY_LATENCY_SUMMARY', summary=weekly_summary)
save_alert_state(state)
save_coordination_state(coordination_state)
activity('RUN_COMPLETE')
