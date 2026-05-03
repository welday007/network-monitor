#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
import json
import re
import subprocess
import statistics
import urllib.parse
import urllib.request

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
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp: resp.read()
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
                payload.setdefault('internet_down_active', False)
                payload.setdefault('router_down_active', False)
                return payload
        except Exception: pass
    return {
        'anomaly_counts': {},
        'anomaly_active': {},
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
                return payload
        except Exception:
            pass
    return {host: [] for host in TARGETS}

def save_latency_history(history: dict) -> None:
    LATENCY_HISTORY.write_text(json.dumps(history, indent=2), encoding='utf-8')

def append_latency_sample(history: dict, host: str, status: str, rtt: float | None) -> None:
    samples = [item for item in history.get(host, []) if isinstance(item, dict)]
    samples.append({
        'time': datetime.now().isoformat(timespec='seconds'),
        'status': status,
        'rtt': rtt,
    })
    history[host] = samples[-LATENCY_SAMPLE_LIMIT:]

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
        return ('Public internet targets are down, but the local router is still reachable.', 'This strongly suggests an upstream Verizon or WAN outage rather than an internal Wi-Fi issue.', 'Check the Verizon gateway internet status and WAN indicators first.')
    if event == 'INTERNET_RECOVERY':
        return ('Public internet reachability has returned while the router remains healthy.', 'The upstream outage appears to have cleared.', 'No action is needed unless the outage returns.')
    if event == 'ROUTER_DOWN':
        return ('The Dell cannot reach the local router.', 'This usually means a local network issue, router reboot, or cable or Wi-Fi problem.', 'Check whether the Verizon router is powered on and whether the Dell still has a LAN link.')
    if event == 'ROUTER_RECOVERY':
        return ('The Dell can reach the local router again.', 'The local gateway path appears to have recovered.', 'No action is needed unless the router drops again soon.')
    if event == 'LATENCY_ANOMALY':
        return (f'Latency to {host} is materially above its normal baseline.', 'This looks like a statistically unusual slowdown rather than ordinary jitter.', 'Watch whether the WAN remains degraded and check the gateway if other services feel slow.')
    if event == 'LATENCY_NORMALIZED':
        return (f'Latency to {host} has returned to its normal range.', 'The earlier abnormal slowdown appears to have cleared.', 'No action is needed unless the anomaly returns.')
    if event == 'STATE_CHANGE' and host != '192.168.1.1' and router == 'UP' and cloudflare == 'DOWN' and google == 'DOWN':
        return ('Public internet targets are down, but the local router is still reachable.', 'This usually points to a Verizon or upstream internet issue rather than a home Wi-Fi problem.', 'Check the Verizon gateway internet status and WAN indicators first.')
    if event == 'STATE_CHANGE' and host == '192.168.1.1' and router == 'DOWN':
        return ('The Dell cannot reach the local router.', 'This usually means a local network issue, router reboot, or cable or Wi-Fi problem.', 'Check whether the Verizon router is powered on and whether the Dell still has a LAN link.')
    if event == 'HIGH_LATENCY':
        return (f'Latency to {host} is running high.', 'This can mean congestion, weak upstream connectivity, or a temporary Verizon slowdown.', 'If the router is healthy, watch whether the public targets stay slow for several minutes.')
    if event == 'LATENCY_RECOVERY':
        return (f'Latency to {host} has recovered.', 'The earlier slowdown appears to have cleared.', 'No action is needed unless the spikes keep returning.')
    return (f'{host} changed state.', 'This is a network state change that may be temporary.', 'Recheck the router and internet targets if it happens again.')

def request_kitt_message(api_key: str, model: str, event: str, host: str, rtt: float | None, summary: str, cause: str, check: str) -> str:
    prompt = ('Write a short Telegram alert as a calm in-car mission computer. Be concise, composed, and protective. Avoid direct imitation of any existing character. Use plain English, under 85 words, no markdown, no emojis. Make it 3 short lines: situation, likely cause, recommended check. Address Kevin once if natural. ' f'Event: {event}. Host: {host}. RTT: {rtt if rtt is not None else "NA"}. ' f'Summary: {summary} Cause: {cause} Check: {check}')
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.45}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp: body = json.loads(resp.read().decode('utf-8'))
    return body['choices'][0]['message']['content'].strip()

def fallback_kitt_message(host: str, summary: str, cause: str, check: str) -> str:
    return '\n'.join([f'KITT: Kevin, I am detecting a network event involving {host}.', f'Assessment: {cause}', f'Recommended check: {check}'])

def request_jarvis_followup(api_key: str, model: str, payload: dict) -> str:
    prompt = (
        'Write one short Telegram follow-up as a polished formal operations assistant with dry wit. '
        'Avoid direct imitation of any existing character. Under 75 words. '
        'Acknowledge KITT has already raised the alert, explain what it means, and suggest the next check. '
        f"Event: {payload.get('event')}. Host: {payload.get('host')}. Status: {payload.get('status')}. RTT: {payload.get('rtt')}. "
        f"Summary: {payload.get('summary')} Cause: {payload.get('cause')} Check: {payload.get('check')}"
    )
    body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.35}
    req = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions', data=json.dumps(body).encode('utf-8'), headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        response = json.loads(resp.read().decode('utf-8'))
    return response['choices'][0]['message']['content'].strip()

def fallback_jarvis_followup(payload: dict) -> str:
    return '\n'.join([
        'Jarvis operations note.',
        payload.get('summary', 'A network condition changed.'),
        f"Recommended check: {payload.get('check', 'Review the router and upstream connectivity.')}",
    ])

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
                summary, cause, check = summarize_context(host, 'LATENCY_ANOMALY', rtt, probes)
                payload = {'time': datetime.now().isoformat(timespec='seconds'), 'event': 'LATENCY_ANOMALY', 'host': host, 'status': status, 'rtt': rtt, 'summary': summary, 'cause': cause, 'check': check, 'mean': stats['mean'], 'stdev': stats['stdev'], 'z_score': z_score, 'probes': {k: {'status': v[0], 'rtt': v[1]} for k, v in probes.items()}}
                write_latest_alert(payload)
                remember_event('monitor', payload)
                log(f'LATENCY_ANOMALY host={host} rtt={rtt}ms mean={stats["mean"]:.1f} stdev={stats["stdev"]:.1f} z={z_score:.2f}')
                activity('LATENCY_ANOMALY', host=host, rtt=rtt, mean=stats['mean'], stdev=stats['stdev'], z_score=z_score)
                anomaly_active[host] = True
                maybe_send_jarvis_group_followup(jarvis_token, jarvis_group_chat_id, api_key, model, payload, coordination_level, coordination_state)
        else:
            anomaly_counts[host] = 0
            if active:
                summary, cause, check = summarize_context(host, 'LATENCY_NORMALIZED', rtt, probes)
                payload = {'time': datetime.now().isoformat(timespec='seconds'), 'event': 'LATENCY_NORMALIZED', 'host': host, 'status': status, 'rtt': rtt, 'summary': summary, 'cause': cause, 'check': check, 'mean': stats['mean'], 'stdev': stats['stdev'], 'z_score': z_score, 'probes': {k: {'status': v[0], 'rtt': v[1]} for k, v in probes.items()}}
                write_latest_alert(payload)
                remember_event('monitor', payload)
                log(f'LATENCY_NORMALIZED host={host} rtt={rtt}ms mean={stats["mean"]:.1f} stdev={stats["stdev"]:.1f} z={z_score:.2f}')
                activity('LATENCY_NORMALIZED', host=host, rtt=rtt, mean=stats['mean'], stdev=stats['stdev'], z_score=z_score)
            anomaly_active[host] = False
    else:
        anomaly_counts[host] = 0
save_latency_history(latency_history)
save_alert_state(state)
save_coordination_state(coordination_state)
activity('RUN_COMPLETE')
