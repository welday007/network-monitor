#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import List
from urllib.parse import parse_qs


HOST = os.environ.get("JARVIS_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("JARVIS_DASHBOARD_PORT", "8085"))
DASHBOARD_MODE = os.environ.get("JARVIS_DASHBOARD_MODE", "ssh").lower()
SSH_TARGET = os.environ.get("JARVIS_SSH_TARGET", "jarvis")
SSH_KEY = os.environ.get("JARVIS_SSH_KEY")
LOCAL_BASE = Path(os.environ.get("JARVIS_MONITOR_BASE", "/home/jarvis/monitor"))
PERSONA_FILES = {
    "jarvis_soul": "/home/jarvis/monitor/personas/jarvis/soul.md",
    "jarvis_skills": "/home/jarvis/monitor/personas/jarvis/skills.md",
    "kitt_soul": "/home/jarvis/monitor/personas/kitt/soul.md",
    "kitt_skills": "/home/jarvis/monitor/personas/kitt/skills.md",
    "shared_memory": "/home/jarvis/monitor/shared_memory.json",
}
CHAT_LOGS = {
    "jarvis": "/home/jarvis/monitor/jarvis_chat.jsonl",
    "kitt": "/home/jarvis/monitor/kitt_chat.jsonl",
}
ADMIN_LOG_PATH = "/var/log/monitor-admin.log"
MARKDOWN_FILES = {
    "README.md": "/home/jarvis/monitor/README.md",
    "network_monitor_install_prompt.md": "/home/jarvis/monitor/network_monitor_install_prompt.md",
    "migration_checklist.md": "/home/jarvis/monitor/migration_checklist.md",
    "monitor_admin.md": "/home/jarvis/monitor/monitor_admin.md",
    "jarvis soul": "/home/jarvis/monitor/personas/jarvis/soul.md",
    "jarvis skills": "/home/jarvis/monitor/personas/jarvis/skills.md",
    "kitt soul": "/home/jarvis/monitor/personas/kitt/soul.md",
    "kitt skills": "/home/jarvis/monitor/personas/kitt/skills.md",
}
RANGE_WINDOWS = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
}


def ssh_base() -> List[str]:
    cmd = ["ssh", "-o", "BatchMode=yes"]
    if SSH_KEY:
        cmd.extend(["-i", SSH_KEY])
    cmd.append(SSH_TARGET)
    return cmd


def run_ssh(command: str) -> str:
    proc = subprocess.run(
        ssh_base() + [command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "SSH command failed")
    return proc.stdout


def probe_hosts(state_dir: Path, hosts: List[str]) -> list[dict]:
    items = []
    for host in hosts:
        state_file = state_dir / host
        status = state_file.read_text().strip() if state_file.exists() else "UNKNOWN"
        proc = subprocess.run(["fping", "-c1", "-t1500", host], capture_output=True, text=True)
        output = (proc.stdout or "") + (proc.stderr or "")
        match = re.search(r"([0-9]+(?:\.[0-9]+)?) ms", output)
        items.append({"host": host, "status": status, "rtt": match.group(1) if match else "NA"})
    return items


def load_personas(persona_files: dict[str, Path]) -> dict:
    payload = {}
    for key, path in persona_files.items():
        payload[key] = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    return payload


def load_heartbeats(heartbeat_files: dict[str, Path]) -> dict:
    payload = {}
    for key, path in heartbeat_files.items():
        if path.exists():
            try:
                payload[key] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload[key] = {"status": "unknown", "updated_at": "invalid heartbeat"}
        else:
            payload[key] = {"status": "missing", "updated_at": "never"}
    return payload

def load_chat_log(path: Path, limit: int = 12) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    items = []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def read_local_payload() -> dict:
    persona_files = {
        "jarvis_soul": LOCAL_BASE / "personas/jarvis/soul.md",
        "jarvis_skills": LOCAL_BASE / "personas/jarvis/skills.md",
        "kitt_soul": LOCAL_BASE / "personas/kitt/soul.md",
        "kitt_skills": LOCAL_BASE / "personas/kitt/skills.md",
        "shared_memory": LOCAL_BASE / "shared_memory.json",
    }
    heartbeat_files = {
        "jarvis": LOCAL_BASE / "personas/jarvis/heartbeat.json",
        "kitt": LOCAL_BASE / "personas/kitt/heartbeat.json",
    }
    chat_logs = {
        "jarvis": LOCAL_BASE / "jarvis_chat.jsonl",
        "kitt": LOCAL_BASE / "kitt_chat.jsonl",
    }
    hosts = ["192.168.1.1", "1.1.1.1", "8.8.8.8"]
    log_path = LOCAL_BASE / "net-health.log"
    metrics = {
        "hosts": probe_hosts(LOCAL_BASE / "state", hosts),
        "recent_events": log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-15:] if log_path.exists() else [],
        "personas": load_personas(persona_files),
        "heartbeats": load_heartbeats(heartbeat_files),
        "chats": {name: load_chat_log(path) for name, path in chat_logs.items()},
        "latency_history": json.loads((LOCAL_BASE / "latency_history.json").read_text(encoding="utf-8")) if (LOCAL_BASE / "latency_history.json").exists() else {},
    }
    vnstat = json.loads(subprocess.run(["vnstat", "--json"], capture_output=True, text=True, timeout=30).stdout)
    return {"metrics": metrics, "vnstat": vnstat}


def read_ssh_payload() -> dict:
    script = r"""python3 - <<'PY'
from pathlib import Path
import json
import re
import subprocess

persona_files = {
    "jarvis_soul": Path("/home/jarvis/monitor/personas/jarvis/soul.md"),
    "jarvis_skills": Path("/home/jarvis/monitor/personas/jarvis/skills.md"),
    "kitt_soul": Path("/home/jarvis/monitor/personas/kitt/soul.md"),
    "kitt_skills": Path("/home/jarvis/monitor/personas/kitt/skills.md"),
    "shared_memory": Path("/home/jarvis/monitor/shared_memory.json"),
}
heartbeat_files = {
    "jarvis": Path("/home/jarvis/monitor/personas/jarvis/heartbeat.json"),
    "kitt": Path("/home/jarvis/monitor/personas/kitt/heartbeat.json"),
}
chat_logs = {
    "jarvis": Path("/home/jarvis/monitor/jarvis_chat.jsonl"),
    "kitt": Path("/home/jarvis/monitor/kitt_chat.jsonl"),
}
hosts = ["192.168.1.1", "1.1.1.1", "8.8.8.8"]
state_dir = Path("/home/jarvis/monitor/state")
log_path = Path("/home/jarvis/monitor/net-health.log")
latency_history = Path("/home/jarvis/monitor/latency_history.json")

payload = {"hosts": [], "recent_events": [], "personas": {}, "heartbeats": {}, "chats": {}, "latency_history": {}}
for host in hosts:
    state_file = state_dir / host
    status = state_file.read_text().strip() if state_file.exists() else "UNKNOWN"
    proc = subprocess.run(["fping", "-c1", "-t1500", host], capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?) ms", output)
    payload["hosts"].append({"host": host, "status": status, "rtt": match.group(1) if match else "NA"})

if log_path.exists():
    payload["recent_events"] = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-15:]

for key, path in persona_files.items():
    payload["personas"][key] = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""

for key, path in heartbeat_files.items():
    if path.exists():
        try:
            payload["heartbeats"][key] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload["heartbeats"][key] = {"status": "unknown", "updated_at": "invalid heartbeat"}
    else:
        payload["heartbeats"][key] = {"status": "missing", "updated_at": "never"}

for key, path in chat_logs.items():
    items = []
    if path.exists():
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines()[-12:]:
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if isinstance(item, dict):
                items.append(item)
    payload["chats"][key] = items

if latency_history.exists():
    try:
        payload["latency_history"] = json.loads(latency_history.read_text(encoding="utf-8"))
    except Exception:
        payload["latency_history"] = {}

print(json.dumps(payload))
PY"""
    metrics = json.loads(run_ssh(script))
    vnstat = json.loads(run_ssh("vnstat --json"))
    return {"metrics": metrics, "vnstat": vnstat}


def read_payload() -> dict:
    if DASHBOARD_MODE == "local":
        return read_local_payload()
    return read_ssh_payload()


def save_local_file(remote_path: str, content: str) -> None:
    path = Path(remote_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def save_ssh_file(remote_path: str, content: str) -> None:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    command = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "import base64\n"
        f"path = Path({remote_path!r})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"content = base64.b64decode({encoded!r}).decode('utf-8')\n"
        "path.write_text(content, encoding='utf-8')\n"
        "PY"
    )
    run_ssh(command)


def save_file(remote_path: str, content: str) -> None:
    if DASHBOARD_MODE == "local":
        save_local_file(remote_path, content)
        return
    save_ssh_file(remote_path, content)


def read_text_file(path_str: str) -> str:
    path = Path(path_str)
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def read_remote_text_file(path_str: str) -> str:
    command = (
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"path = Path({path_str!r})\n"
        "print(path.read_text(encoding='utf-8', errors='ignore') if path.exists() else '')\n"
        "PY"
    )
    return run_ssh(command)


def read_markdown_file(path_str: str) -> str:
    if DASHBOARD_MODE == "local":
        return read_text_file(path_str)
    return read_remote_text_file(path_str)


def latest_traffic(vnstat: dict) -> dict:
    return interface_traffic(vnstat, None)


def interface_traffic(vnstat: dict, interface_name: str | None) -> dict:
    interfaces = vnstat.get("interfaces", [])
    if not interfaces:
        return {"name": interface_name or "unknown", "rx": "n/a", "tx": "n/a"}
    iface = None
    if interface_name:
        iface = next((item for item in interfaces if item.get("name") == interface_name), None)
    if iface is None:
        iface = interfaces[0]
    days = iface.get("traffic", {}).get("day", [])
    if not days:
        return {"name": iface.get("name", interface_name or "unknown"), "rx": "n/a", "tx": "n/a"}
    day = days[-1]
    return {
        "name": iface.get("name", interface_name or "unknown"),
        "rx": format_bytes(day.get("rx", 0)),
        "tx": format_bytes(day.get("tx", 0)),
    }


def format_bytes(value: int) -> str:
    units = ["KiB", "MiB", "GiB", "TiB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    return f"{size:.1f} {unit}"


def render_status_card(item: dict) -> str:
    tier = "critical" if item["status"] != "UP" else "healthy"
    return (
        f'<article class="card {tier}">'
        f'<div class="label">{html.escape(item["host"])}</div>'
        f'<div class="status">{html.escape(item["status"])}</div>'
        f'<div class="meta">RTT {html.escape(item["rtt"])} ms</div>'
        "</article>"
    )


def render_heartbeat(name: str, heartbeat: dict) -> str:
    status = heartbeat.get("status", "ok")
    updated = heartbeat.get("updated_at", "never")
    source = heartbeat.get("source", "unknown")
    return (
        '<article class="panel">'
        f'<div class="label">{html.escape(name.upper())} heartbeat</div>'
        f'<div class="statusline"><strong>{html.escape(status)}</strong></div>'
        f'<div class="meta">Updated: {html.escape(updated)}</div>'
        f'<div class="meta">Source: {html.escape(source)}</div>'
        "</article>"
    )


def event_severity(line: str) -> str:
    upper = line.upper()
    if any(token in upper for token in ("ROUTER_DOWN", "INTERNET_OUTAGE", "WIFI_BACKUP_ALERT_SENT", "FAIL", "ERROR")):
        return "critical"
    if any(token in upper for token in ("LATENCY_ANOMALY", "STATE_CHANGE", "WIFI_BACKUP_CHECK")):
        return "warning"
    return "normal"


def render_recent_events(lines: list[str]) -> str:
    items = []
    for line in reversed(lines):
        severity = event_severity(line)
        items.append(f'<li class="event {severity}">{html.escape(line)}</li>')
    content = "".join(items) or '<li class="event normal">No recent events</li>'
    return '<ul class="eventlog">' + content + '</ul>'


def render_editor(field: str, title: str, value: str, notice: str = "") -> str:
    return f"""
    <section class="panel editor">
      <div class="editor-head">
        <div>
          <div class="label">{html.escape(title)}</div>
          {f'<div class="meta notice">{html.escape(notice)}</div>' if notice else ''}
        </div>
      </div>
      <form method="post" action="/save">
        <input type="hidden" name="field" value="{html.escape(field)}">
        <textarea name="content">{html.escape(value)}</textarea>
        <div class="formbar">
          <button type="submit">Save Remote File</button>
        </div>
      </form>
    </section>
    """

def latency_summary(samples: list[dict]) -> tuple[str, str, float | None, float | None]:
    usable = [float(item["rtt"]) for item in samples if item.get("status") == "UP" and isinstance(item.get("rtt"), (int, float))]
    if not usable:
        return ("n/a", "n/a", None, None)
    mean = sum(usable) / len(usable)
    if len(usable) < 2:
        return (f"{mean:.1f} ms", "0.0 ms", mean, 0.0)
    variance = sum((value - mean) ** 2 for value in usable) / (len(usable) - 1)
    stdev = variance ** 0.5
    return (f"{mean:.1f} ms", f"{stdev:.1f} ms", mean, stdev)

def filter_latency_samples(samples: list[dict], range_key: str) -> list[dict]:
    window = RANGE_WINDOWS.get(range_key, RANGE_WINDOWS["day"])
    cutoff = datetime.now() - window
    filtered = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("time", "")).strip()
        try:
            ts = datetime.fromisoformat(raw)
        except Exception:
            continue
        if ts >= cutoff:
            filtered.append(item)
    return filtered

def render_range_tabs(active_range: str) -> str:
    tabs = []
    for key, label in (("hour", "Hour"), ("day", "Day"), ("week", "Week"), ("month", "Month"), ("year", "Year")):
        klass = "tab active" if key == active_range else "tab"
        tabs.append(f'<a class="{klass}" href="/?range={key}">{label}</a>')
    return '<section class="tabs">' + "".join(tabs) + '</section>'


def render_main_tabs(active_page: str, range_key: str, active_doc: str) -> str:
    overview_href = f"/?page=overview&range={range_key}"
    docs_href = f"/?page=docs&doc={active_doc}"
    admin_href = "/?page=admin"
    matrix_href = "/?page=matrix"
    tabs = [
        f'<a class="tab {"active" if active_page == "overview" else ""}" href="{overview_href}">Overview</a>',
        f'<a class="tab {"active" if active_page == "docs" else ""}" href="{docs_href}">Files</a>',
        f'<a class="tab {"active" if active_page == "admin" else ""}" href="{admin_href}">Admin</a>',
        f'<a class="tab {"active" if active_page == "matrix" else ""}" href="{matrix_href}">Matrix</a>',
    ]
    return '<section class="tabs">' + "".join(tabs) + '</section>'


def read_admin_entries() -> list[dict]:
    try:
        raw = read_markdown_file(ADMIN_LOG_PATH)
    except Exception:
        return []
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries[-300:]


def render_admin_page() -> str:
    entries = list(reversed(read_admin_entries()))
    cards = []
    for item in entries:
        ok = bool(item.get("ok"))
        klass = "ok" if ok else "fail"
        action = str(item.get("action", "unknown"))
        argv = item.get("argv", [])
        if not isinstance(argv, list):
            argv = []
        command = "monitor-admin " + " ".join([action] + [str(part) for part in argv])
        result = item.get("result", {})
        result_text = html.escape(json.dumps(result, indent=2, ensure_ascii=True, default=str))
        message = html.escape(str(item.get("message", "")))
        timestamp = html.escape(str(item.get("time", "")))
        cards.append(
            '<article class="panel admin-entry ' + klass + '">'
            + f'<div class="doc-head"><div class="label">{timestamp}</div><div class="statusline"><strong>{"OK" if ok else "FAIL"}</strong></div></div>'
            + f'<div class="meta"><code>{html.escape(command)}</code></div>'
            + (f'<div class="meta">{message}</div>' if message else '')
            + f'<pre class="admin-result">{result_text}</pre>'
            + '</article>'
        )
    content = "".join(cards) or '<section class="panel"><div class="label">Admin</div><div class="meta">No monitor-admin history yet.</div></section>'
    return (
        '<section class="panel"><div class="label">Monitor Admin</div>'
        '<div class="meta">Live wrapper command history and result previews from <code>/var/log/monitor-admin.log</code>.</div></section>'
        + '<section class="admin-grid">' + content + '</section>'
    )


def render_matrix_page() -> str:
    return """
    <section class="panel matrix-panel">
      <div class="label">Matrix</div>
      <div class="meta">Mouse movement disturbs the rain.</div>
      <canvas id="matrix-canvas"></canvas>
    </section>
    """


def markdown_to_html(text: str) -> str:
    lines = text.splitlines()
    out = []
    in_list = False
    for raw in lines:
        line = raw.rstrip()
        if not line:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if line.startswith("### "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{html.escape(line[4:])}</h3>")
            continue
        if line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
            continue
        if line.startswith("# "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
            continue
        if line.startswith("- "):
            if not in_list:
                out.append('<ul class="mdlist">')
                in_list = True
            out.append(f"<li>{html.escape(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out) or "<p>No content.</p>"


def render_docs_page(active_doc: str) -> str:
    if active_doc not in MARKDOWN_FILES:
        active_doc = next(iter(MARKDOWN_FILES))
    links = []
    for label in MARKDOWN_FILES:
        klass = "tab active" if label == active_doc else "tab"
        links.append(f'<a class="{klass}" href="/?page=docs&doc={label}">{html.escape(label)}</a>')
    content = read_markdown_file(MARKDOWN_FILES[active_doc])
    rendered = markdown_to_html(content)
    return (
        '<section class="tabs">' + "".join(links) + '</section>'
        + '<section class="split">'
        + '<section class="panel"><div class="label">Markdown Files</div>'
        + '<div class="meta">Open a file, then click Fullscreen on the right pane.</div>'
        + "".join(f'<div class="meta">{html.escape(label)}: <code>{html.escape(path)}</code></div>' for label, path in MARKDOWN_FILES.items())
        + '</section>'
        + (
            f'<section class="panel doc-panel" id="doc-panel">'
            f'<div class="doc-head"><div class="label">{html.escape(active_doc)}</div>'
            '<button type="button" onclick="toggleDocFullscreen()">Fullscreen</button></div>'
            f'<div class="markdown-body">{rendered}</div></section>'
        )
        + '</section>'
    )

def render_latency_chart(host: str, samples: list[dict], range_key: str) -> str:
    scoped = filter_latency_samples(samples, range_key)
    values = [float(item["rtt"]) for item in scoped if item.get("status") == "UP" and isinstance(item.get("rtt"), (int, float))][-120:]
    avg_text, std_text, mean, stdev = latency_summary(scoped)
    threshold = (mean + (2 * stdev)) if mean is not None and stdev is not None else None
    anomaly_count = sum(1 for value in values if threshold is not None and value >= threshold)
    if len(values) < 2:
        return (
            '<section class="panel">'
            f'<div class="label">{html.escape(host)} latency</div>'
            f'<div class="meta">Average: {html.escape(avg_text)}</div>'
            f'<div class="meta">Std dev: {html.escape(std_text)}</div>'
            f'<div class="meta">Over red dashed: {anomaly_count}</div>'
            '<div class="meta">Alert threshold: mean + 2 sigma</div>'
            '<div class="meta">Not enough history yet for a chart.</div>'
            '</section>'
        )
    min_v = min(values + ([mean] if mean is not None else []) + ([threshold] if threshold is not None else []))
    max_v = max(values + ([mean] if mean is not None else []) + ([threshold] if threshold is not None else []))
    spread = max(max_v - min_v, 1.0)
    points = []
    anomaly_points = []
    for idx, value in enumerate(values):
        x = 10 + (280 * idx / max(len(values) - 1, 1))
        y = 110 - (((value - min_v) / spread) * 90)
        points.append(f"{x:.1f},{y:.1f}")
        if threshold is not None and value >= threshold:
            anomaly_points.append((x, y))
    polyline = " ".join(points)
    mean_y = 110 - ((((mean or min_v) - min_v) / spread) * 90) if mean is not None else None
    threshold_y = 110 - (((threshold - min_v) / spread) * 90) if threshold is not None else None
    anomaly_markup = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#ff6b86" />' for x, y in anomaly_points)
    line_markup = ""
    if mean_y is not None:
        line_markup += f'<line x1="10" x2="290" y1="{mean_y:.1f}" y2="{mean_y:.1f}" stroke="#7dd3fc" stroke-width="2" stroke-dasharray="6 4" />'
    if threshold_y is not None:
        line_markup += f'<line x1="10" x2="290" y1="{threshold_y:.1f}" y2="{threshold_y:.1f}" stroke="#ff6b86" stroke-width="2" stroke-dasharray="8 5" />'
    return (
        '<section class="panel">'
        f'<div class="label">{html.escape(host)} latency</div>'
        f'<div class="meta">Average: {html.escape(avg_text)}</div>'
        f'<div class="meta">Std dev: {html.escape(std_text)}</div>'
        f'<div class="meta">Over red dashed: {anomaly_count}</div>'
        '<div class="meta">Blue dashed: average. Red dashed: average + 2 sigma. Red dots: non-critical anomalies.</div>'
        f'<svg viewBox="0 0 300 120" class="chart">{line_markup}<polyline fill="none" stroke="#4cf2c2" stroke-width="3" points="{polyline}"/>{anomaly_markup}</svg>'
        '</section>'
    )

def render_chat_panel(name: str, items: list[dict]) -> str:
    recent = list(reversed(items[-5:]))
    older = list(reversed(items[:-5]))
    rows = []
    for item in recent:
        direction = "out" if item.get("direction") == "out" else "in"
        klass = "out" if direction == "out" else "in"
        text = html.escape(str(item.get("text", "")))
        ts = html.escape(str(item.get("time", "")))
        rows.append(f'<li class="{klass}"><span>{text}</span><small>{ts}</small></li>')
    content = "".join(rows) or '<li class="in"><span>No recent chat history</span></li>'
    older_rows = []
    for item in older:
        direction = "out" if item.get("direction") == "out" else "in"
        klass = "out" if direction == "out" else "in"
        text = html.escape(str(item.get("text", "")))
        ts = html.escape(str(item.get("time", "")))
        older_rows.append(f'<li class="{klass}"><span>{text}</span><small>{ts}</small></li>')
    older_markup = ""
    if older_rows:
        older_markup = '<div class="meta">Older</div><ul class="chatlog chatlog-scroll">' + "".join(older_rows) + '</ul>'
    return (
        '<section class="panel">'
        f'<div class="label">{html.escape(name.upper())} recent chat</div>'
        f'<ul class="chatlog">{content}</ul>{older_markup}'
        '</section>'
    )


def render_page(payload: dict, flash: str = "", range_key: str = "day", page: str = "overview", active_doc: str = "README.md") -> str:
    metrics = payload["metrics"]
    eth0_traffic = interface_traffic(payload["vnstat"], "eth0")
    wifi_traffic = interface_traffic(payload["vnstat"], "wlan1")
    persona_data = metrics["personas"]
    heartbeats = metrics["heartbeats"]
    chats = metrics.get("chats", {})
    latency_history = metrics.get("latency_history", {})
    cards = "".join(render_status_card(item) for item in metrics["hosts"])
    recent_events = render_recent_events(metrics["recent_events"])
    charts = "".join(render_latency_chart(host, latency_history.get(host, []), range_key) for host in ("192.168.1.1", "1.1.1.1", "8.8.8.8"))
    chat_panels = render_chat_panel("jarvis", chats.get("jarvis", [])) + render_chat_panel("kitt", chats.get("kitt", []))
    transport = "directly on this machine" if DASHBOARD_MODE == "local" else "to the Dell over SSH"
    storage_label = "Local Files" if DASHBOARD_MODE == "local" else "Remote Files"
    main_tabs = render_main_tabs(page, range_key, active_doc)
    overview_markup = f"""
    {render_range_tabs(range_key)}
    <section class="triple charts-top">
      {charts}
    </section>
    <section class="panel">
      <div class="label">Recent Events</div>
      {recent_events}
    </section>
    <section class="split">
      {chat_panels}
    </section>
    <section class="grid">
      <section class="panel">
        <div class="label">Today on eth0</div>
        <div class="statusline"><strong>{html.escape(eth0_traffic["rx"])} down</strong></div>
        <div class="meta">{html.escape(eth0_traffic["tx"])} up</div>
      </section>
      <section class="panel">
        <div class="label">Today on wifi</div>
        <div class="statusline"><strong>{html.escape(wifi_traffic["rx"])} down</strong></div>
        <div class="meta">{html.escape(wifi_traffic["tx"])} up</div>
      </section>
      <section class="panel">
        <div class="label">Current Targets</div>
        <div class="cards" style="margin-top:18px">{cards}</div>
      </section>
    </section>
    <section class="split">
      {render_heartbeat("jarvis", heartbeats.get("jarvis", {}))}
      {render_heartbeat("kitt", heartbeats.get("kitt", {}))}
    </section>
    <section class="triple">
      {render_editor("jarvis_soul", "Jarvis Soul", persona_data.get("jarvis_soul", ""))}
      {render_editor("jarvis_skills", "Jarvis Skills", persona_data.get("jarvis_skills", ""))}
      {render_editor("kitt_soul", "KITT Soul", persona_data.get("kitt_soul", ""))}
    </section>
    <section class="split">
      {render_editor("kitt_skills", "KITT Skills", persona_data.get("kitt_skills", ""))}
      {render_editor("shared_memory", "Shared Memory", persona_data.get("shared_memory", ""), notice="Shared state for Jarvis, KITT, and the monitor.")}
    </section>
    <section class="split">
      <section class="panel">
        <div class="label">{html.escape(storage_label)}</div>
        <div class="meta">Jarvis soul: <code>{html.escape(PERSONA_FILES["jarvis_soul"])}</code></div>
        <div class="meta">Jarvis skills: <code>{html.escape(PERSONA_FILES["jarvis_skills"])}</code></div>
        <div class="meta">KITT soul: <code>{html.escape(PERSONA_FILES["kitt_soul"])}</code></div>
        <div class="meta">KITT skills: <code>{html.escape(PERSONA_FILES["kitt_skills"])}</code></div>
        <div class="meta">Shared memory: <code>{html.escape(PERSONA_FILES["shared_memory"])}</code></div>
        <div class="meta" style="margin-top:14px;">Auto-refresh is every 20 seconds. Heartbeats show whether the bot processes are still checking in.</div>
      </section>
    </section>
    """
    docs_markup = render_docs_page(active_doc)
    admin_markup = render_admin_page()
    matrix_markup = render_matrix_page()
    if page == "docs":
        body_markup = docs_markup
    elif page == "admin":
        body_markup = admin_markup
    elif page == "matrix":
        body_markup = matrix_markup
    else:
        body_markup = overview_markup
    refresh_meta = '' if page == "matrix" else '<meta http-equiv="refresh" content="20">'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {refresh_meta}
  <title>Jarvis Command Deck</title>
  <style>
    :root {{
      --bg: #061017;
      --bg2: #0b1722;
      --panel: rgba(11, 26, 38, 0.88);
      --line: rgba(103, 223, 255, 0.18);
      --ink: #e6fbff;
      --muted: #86b6c6;
      --accent: #4cf2c2;
      --bad: #ff6b86;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Consolas, "Courier New", monospace;
      color: var(--ink);
      background:
        radial-gradient(circle at 10% 20%, rgba(76,242,194,0.12), transparent 22%),
        radial-gradient(circle at 90% 10%, rgba(75,182,255,0.14), transparent 20%),
        linear-gradient(180deg, var(--bg), var(--bg2));
    }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 20px 40px; }}
    h1 {{ margin: 0; font-size: 2.5rem; letter-spacing: 0.12em; text-transform: uppercase; }}
    .sub {{ color: var(--muted); margin-top: 10px; max-width: 760px; line-height: 1.5; }}
    .flash {{
      margin-top: 18px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 14px;
      background: rgba(76,242,194,0.08); color: var(--accent);
    }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 18px; margin-top: 20px; }}
    .split {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 18px; }}
    .triple {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 18px; }}
    .charts-top {{ max-width: 1160px; margin: 0 auto; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .panel, .card {{
      background: linear-gradient(180deg, rgba(14, 31, 46, 0.95), var(--panel));
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
      box-shadow: 0 18px 40px rgba(0,0,0,0.28);
    }}
    .card.critical {{ border-left: 8px solid var(--bad); }}
    .card.healthy {{ border-left: 8px solid var(--accent); }}
    .label {{ color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; font-size: 0.82rem; }}
    .status {{ font-size: 1.9rem; margin-top: 10px; }}
    .statusline strong {{ color: var(--accent); font-size: 1.15rem; }}
    .meta {{ color: var(--muted); margin-top: 8px; line-height: 1.45; }}
    ul {{ margin: 10px 0 0; padding-left: 20px; }}
    li {{ margin: 8px 0; line-height: 1.45; }}
    .editor textarea {{
      width: 100%; min-height: 180px; margin-top: 12px; resize: vertical;
      border-radius: 14px; border: 1px solid var(--line); padding: 12px;
      background: rgba(0,0,0,0.22); color: var(--ink); font: inherit;
    }}
    .formbar {{ margin-top: 12px; display: flex; justify-content: flex-end; }}
    button {{
      background: linear-gradient(90deg, #0e7669, #0c4a6e);
      color: white; border: 0; border-radius: 999px; padding: 10px 16px; font: inherit; cursor: pointer;
    }}
    .notice {{ color: var(--accent); }}
    .tabs {{ display: flex; gap: 10px; margin-top: 18px; flex-wrap: wrap; }}
    .tab {{
      color: var(--muted); text-decoration: none; border: 1px solid var(--line);
      border-radius: 999px; padding: 8px 12px; background: rgba(0,0,0,0.18);
    }}
    .tab.active {{ color: var(--ink); background: rgba(76,242,194,0.16); border-color: rgba(76,242,194,0.35); }}
    .chart {{ width: 100%; height: 120px; margin-top: 10px; background: rgba(0,0,0,0.16); border-radius: 12px; }}
    .chatlog {{ list-style: none; padding-left: 0; }}
    .chatlog li {{ display: flex; flex-direction: column; gap: 4px; margin: 10px 0; padding: 10px 12px; border-radius: 12px; }}
    .chatlog li.in {{ background: rgba(76, 242, 194, 0.08); }}
    .chatlog li.out {{ background: rgba(75, 182, 255, 0.12); }}
    .chatlog-scroll {{ max-height: 220px; overflow-y: auto; padding-right: 6px; }}
    .chatlog small {{ color: var(--muted); }}
    .eventlog {{ list-style: none; padding-left: 0; }}
    .event {{ margin: 10px 0; padding: 10px 12px; border-radius: 12px; }}
    .event.normal {{ background: rgba(76, 242, 194, 0.08); }}
    .event.warning {{ background: rgba(255, 202, 87, 0.14); color: #ffe7a3; }}
    .event.critical {{ background: rgba(255, 107, 134, 0.14); color: #ffd1da; }}
    .admin-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 18px; }}
    .admin-entry.ok {{ border-left: 8px solid var(--accent); }}
    .admin-entry.fail {{ border-left: 8px solid var(--bad); }}
    .admin-result {{
      margin-top: 12px; padding: 12px; border-radius: 12px; overflow: auto; max-height: 260px;
      background: rgba(0,0,0,0.22); color: var(--ink); white-space: pre-wrap; word-break: break-word;
    }}
    .markdown-body h1, .markdown-body h2, .markdown-body h3 {{ margin-top: 0; }}
    .markdown-body p, .markdown-body li {{ line-height: 1.55; }}
    .mdlist {{ padding-left: 20px; }}
    .doc-head {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .doc-head button {{
      background: linear-gradient(90deg, #1d4ed8, #0f766e);
      border: 1px solid rgba(255,255,255,0.18);
      box-shadow: 0 8px 18px rgba(0,0,0,0.22);
    }}
    .doc-panel.fullscreen {{
      position: fixed; inset: 16px; z-index: 9999; margin: 0; overflow: auto;
      max-width: none; width: auto; height: auto; box-shadow: 0 24px 80px rgba(0,0,0,0.55);
    }}
    .doc-panel.fullscreen .markdown-body {{ max-width: 1100px; }}
    .matrix-panel {{ min-height: 78vh; padding: 0; overflow: hidden; position: relative; }}
    .matrix-panel .label, .matrix-panel .meta {{ position: absolute; left: 18px; z-index: 2; }}
    .matrix-panel .label {{ top: 18px; }}
    .matrix-panel .meta {{ top: 42px; }}
    #matrix-canvas {{ display: block; width: 100%; height: 78vh; }}
    @media (max-width: 900px) {{
      .grid, .split, .triple, .cards, .admin-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
  <script>
    function toggleDocFullscreen() {{
      const panel = document.getElementById('doc-panel');
      if (!panel) return;
      panel.classList.toggle('fullscreen');
    }}
    function initMatrix() {{
      const canvas = document.getElementById('matrix-canvas');
      if (!canvas || canvas.dataset.active === '1') return;
      canvas.dataset.active = '1';
      const ctx = canvas.getContext('2d');
      const chars = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz@#$%&*+=-<>[]{{}}()/\\\\|';
      const ripples = [];
      let width = 0;
      let height = 0;
      let fontSize = 16;
      let columns = 0;
      let drops = [];

      function resize() {{
        const rect = canvas.getBoundingClientRect();
        width = Math.max(320, Math.floor(rect.width));
        height = Math.max(420, Math.floor(rect.height));
        canvas.width = width;
        canvas.height = height;
        columns = Math.max(1, Math.floor(width / fontSize));
        drops = Array.from({{ length: columns }}, () => Math.random() * height / fontSize);
      }}

      function disturb(event) {{
        const rect = canvas.getBoundingClientRect();
        ripples.push({{
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
          radius: 0,
          life: 1.2
        }});
      }}

      function draw() {{
        ctx.fillStyle = 'rgba(6, 16, 23, 0.12)';
        ctx.fillRect(0, 0, width, height);
        ctx.font = fontSize + 'px Consolas, Courier New, monospace';
        ctx.textBaseline = 'top';

        for (let i = ripples.length - 1; i >= 0; i -= 1) {{
          ripples[i].radius += 18;
          ripples[i].life -= 0.03;
          if (ripples[i].life <= 0) {{
            ripples.splice(i, 1);
          }}
        }}

        for (let i = 0; i < drops.length; i += 1) {{
          const x = i * fontSize;
          let y = drops[i] * fontSize;
          for (const ripple of ripples) {{
            const dx = x - ripple.x;
            const dy = y - ripple.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < ripple.radius + 120) {{
              y -= Math.max(0, 140 - dist) * 0.55;
            }}
          }}
          const ch = chars[Math.floor(Math.random() * chars.length)];
          let glow = 0;
          for (const ripple of ripples) {{
            const dx = x - ripple.x;
            const dy = y - ripple.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            glow = Math.max(glow, Math.max(0, 1 - (dist / 180)) * ripple.life);
          }}
          ctx.fillStyle = glow > 0.15 ? '#e6fbff' : (i % 6 === 0 ? '#7dd3fc' : '#4cf2c2');
          ctx.fillText(ch, x, y);
          drops[i] += 1 + Math.random() * 0.35;
          if (y > height + Math.random() * 800) {{
            drops[i] = -Math.random() * 20;
          }}
        }}
        requestAnimationFrame(draw);
      }}

      resize();
      window.addEventListener('resize', resize);
      canvas.addEventListener('mousemove', disturb);
      canvas.addEventListener('touchmove', (event) => {{
        const touch = event.touches[0];
        if (touch) disturb(touch);
      }}, {{ passive: true }});
      ctx.fillStyle = '#061017';
      ctx.fillRect(0, 0, width, height);
      requestAnimationFrame(draw);
    }}
    window.addEventListener('load', initMatrix);
  </script>
</head>
<body>
  <main class="wrap">
    <h1>Jarvis Command Deck</h1>
    <p class="sub">Live network status, personality files, and edit controls for Jarvis and KITT. Changes save {html.escape(transport)}.</p>
    {f'<div class="flash">{html.escape(flash)}</div>' if flash else ''}
    {main_tabs}
    {body_markup}
  </main>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path_only, _, query_string = self.path.partition("?")
        if path_only not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            flash = ""
            if "?saved=" in self.path:
                flash = "Remote file saved."
            params = parse_qs(query_string)
            range_key = params.get("range", ["day"])[0].lower()
            if range_key not in RANGE_WINDOWS:
                range_key = "day"
            page = params.get("page", ["overview"])[0].lower()
            if page not in ("overview", "docs", "admin", "matrix"):
                page = "overview"
            active_doc = params.get("doc", ["README.md"])[0]
            payload = read_payload()
            body = render_page(payload, flash=flash, range_key=range_key, page=page, active_doc=active_doc).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = f"<h1>Dashboard Error</h1><pre>{html.escape(str(exc))}</pre>".encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/save":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = parse_qs(self.rfile.read(length).decode("utf-8"))
        field = data.get("field", [""])[0]
        content = data.get("content", [""])[0]
        if field not in PERSONA_FILES:
            self.send_error(400, "Unknown field")
            return
        try:
            save_file(PERSONA_FILES[field], content)
            self.send_response(303)
            self.send_header("Location", "/?saved=1")
            self.end_headers()
        except Exception as exc:
            body = f"<h1>Save Error</h1><pre>{html.escape(str(exc))}</pre>".encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    server = ReusableHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard running on http://{HOST}:{PORT} mode={DASHBOARD_MODE}")
    server.serve_forever()
