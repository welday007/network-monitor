#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

MONITOR_BASE = Path("/home/jarvis/monitor")
JARVIS_USER = "jarvis"
ROOT_LOG = Path("/var/log/monitor-admin.log")
LOG_PATHS = {
    "activity": MONITOR_BASE / "activity.log",
    "cron": MONITOR_BASE / "cron.log",
    "dashboard": MONITOR_BASE / "dashboard.out",
    "net": MONITOR_BASE / "net-health.log",
    "start": MONITOR_BASE / "start_bots.log",
    "jarvis": MONITOR_BASE / "jarvis_bot.out",
    "kitt": MONITOR_BASE / "kitt_bot.out",
    "router": MONITOR_BASE / "router_syslog_receiver.out",
    "wifi": MONITOR_BASE / "wifi_backup_state.json",
}
JOB_PATHS = {
    "monitor": MONITOR_BASE / "monitor_net.py",
    "watchdog": MONITOR_BASE / "health_watchdog.py",
    "joke": MONITOR_BASE / "send_joke.py",
    "status": MONITOR_BASE / "send_status.py",
    "wifi-check": MONITOR_BASE / "check_wifi_backup.py",
}
INSTALL_TARGETS = {
    "wpa_wlan1": (MONITOR_BASE / "wpa_supplicant-wlan1.conf", Path("/etc/wpa_supplicant/wpa_supplicant-wlan1.conf"), 0o600),
    "if_wlan1": (MONITOR_BASE / "wlan1.interfaces", Path("/etc/network/interfaces.d/wlan1"), 0o644),
    "rc_local": (MONITOR_BASE / "rc.local", Path("/etc/rc.local"), 0o755),
}
WIFI_IFACES = {"wlan1"}


def respond(ok: bool, action: str, **payload: Any) -> int:
    result = {"ok": ok, "action": action, **payload}
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if ok else 1


def log_admin(action: str, argv: list[str], ok: bool, message: str = "", result: Any | None = None) -> None:
    ROOT_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "argv": argv,
        "ok": ok,
        "message": message,
        "result": result if result is not None else {},
    }
    with ROOT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=True) + "\n")
    os.chmod(ROOT_LOG, 0o644)


def result_preview(payload: Any) -> Any:
    text = json.dumps(payload, ensure_ascii=True, default=str)
    if len(text) <= 900:
        return payload
    return {"preview": text[:900] + "..."}


def ensure_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("monitor-admin must run as root")


def jarvis_preexec() -> None:
    info = pwd.getpwnam(JARVIS_USER)
    os.setgid(info.pw_gid)
    os.setuid(info.pw_uid)
    os.environ["HOME"] = info.pw_dir
    os.environ["USER"] = JARVIS_USER
    os.environ["LOGNAME"] = JARVIS_USER


def run_cmd(
    args: list[str],
    *,
    as_jarvis: bool = False,
    cwd: Path | None = None,
    timeout: int = 60,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        preexec_fn=jarvis_preexec if as_jarvis else None,
    )


def tail_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:]


def process_list() -> list[dict[str, str]]:
    proc = run_cmd(["/usr/bin/ps", "-eo", "pid=,args="], check=False)
    items = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        pid, _, args = line.partition(" ")
        items.append({"pid": pid.strip(), "args": args.strip()})
    return items


def matching_processes(needle: str) -> list[dict[str, str]]:
    return [item for item in process_list() if needle in item["args"]]


def kill_matching(needle: str) -> None:
    for item in matching_processes(needle):
        try:
            os.kill(int(item["pid"]), signal.SIGTERM)
        except Exception:
            continue


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def monitor_status() -> dict[str, Any]:
    ip_info = run_cmd(["/usr/sbin/ip", "-brief", "addr"], check=False).stdout.splitlines()
    return {
        "dashboard_port_8085": port_open(8085),
        "processes": {
            "dashboard": len(matching_processes("/home/jarvis/monitor/dashboard.py")),
            "jarvis_bot": len(matching_processes("/home/jarvis/monitor/jarvis_bot.py")),
            "kitt_bot": len(matching_processes("/home/jarvis/monitor/kitt_bot.py")),
            "router_receiver": len(matching_processes("/home/jarvis/monitor/router_syslog_receiver.py")),
        },
        "ip_brief": ip_info,
    }


def ensure_log_owned(path: Path) -> None:
    info = pwd.getpwnam(JARVIS_USER)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    os.chown(path, info.pw_uid, info.pw_gid)


def restart_dashboard() -> dict[str, Any]:
    run_cmd(["/usr/bin/fuser", "-k", "8085/tcp"], check=False, timeout=10)
    log_path = MONITOR_BASE / "dashboard.out"
    ensure_log_owned(log_path)
    info = pwd.getpwnam(JARVIS_USER)
    env = os.environ.copy()
    env["JARVIS_DASHBOARD_MODE"] = "local"
    env["JARVIS_DASHBOARD_HOST"] = "0.0.0.0"
    env["HOME"] = info.pw_dir
    env["USER"] = JARVIS_USER
    env["LOGNAME"] = JARVIS_USER
    with log_path.open("ab", buffering=0) as fh:
        proc = subprocess.Popen(
            ["/usr/bin/python3", str(MONITOR_BASE / "dashboard.py")],
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
            preexec_fn=jarvis_preexec,
        )
    time.sleep(2)
    return {"pid": proc.pid, "dashboard_port_8085": port_open(8085)}


def restart_bots() -> dict[str, Any]:
    proc = run_cmd(["/bin/sh", str(MONITOR_BASE / "start_bots.sh")], as_jarvis=True, cwd=MONITOR_BASE, timeout=30)
    time.sleep(2)
    return {
        "stdout": proc.stdout.splitlines()[-20:],
        "stderr": proc.stderr.splitlines()[-20:],
        "status": monitor_status()["processes"],
    }


def run_job(name: str) -> dict[str, Any]:
    if name not in JOB_PATHS:
        raise ValueError(f"unknown job: {name}")
    proc = run_cmd(["/usr/bin/python3", str(JOB_PATHS[name])], as_jarvis=True, cwd=MONITOR_BASE, timeout=180, check=False)
    return {
        "job": name,
        "returncode": proc.returncode,
        "stdout": proc.stdout.splitlines()[-30:],
        "stderr": proc.stderr.splitlines()[-30:],
    }


def tail_log(name: str, lines: int) -> dict[str, Any]:
    if name not in LOG_PATHS:
        raise ValueError(f"unknown log: {name}")
    count = max(1, min(lines, 200))
    path = LOG_PATHS[name]
    return {"log": name, "path": str(path), "lines": tail_lines(path, count)}


def show_crontab() -> dict[str, Any]:
    proc = run_cmd(["/usr/bin/crontab", "-l"], as_jarvis=True, cwd=MONITOR_BASE, timeout=30, check=False)
    return {
        "returncode": proc.returncode,
        "lines": proc.stdout.splitlines(),
        "stderr": proc.stderr.splitlines()[-10:],
    }


def install_crontab() -> dict[str, Any]:
    source = MONITOR_BASE / "jarvis.crontab"
    if not source.exists():
        raise FileNotFoundError(str(source))
    proc = run_cmd(["/usr/bin/crontab", str(source)], as_jarvis=True, cwd=MONITOR_BASE, timeout=30)
    return {"source": str(source), "stdout": proc.stdout.splitlines(), "stderr": proc.stderr.splitlines()}


def install_file(target_name: str) -> dict[str, Any]:
    if target_name not in INSTALL_TARGETS:
        raise ValueError(f"unknown install target: {target_name}")
    source, target, mode = INSTALL_TARGETS[target_name]
    if not source.exists():
        if target_name == "wpa_wlan1":
            template = MONITOR_BASE / "wpa_supplicant-wlan1.template.conf"
            raise FileNotFoundError(f"{source} missing; copy {template} to {source} and fill in the Wi-Fi passwords first")
        raise FileNotFoundError(str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    os.chmod(target, mode)
    return {"source": str(source), "target": str(target), "mode": oct(mode)}


def restart_wifi(iface: str) -> dict[str, Any]:
    if iface not in WIFI_IFACES:
        raise ValueError(f"unknown wifi interface: {iface}")
    config = Path(f"/etc/wpa_supplicant/wpa_supplicant-{iface}.conf")
    if not config.exists():
        raise FileNotFoundError(str(config))
    kill_matching(f"wpa_supplicant -B -i {iface}")
    kill_matching(f"wpa_supplicant -i {iface}")
    run_cmd(["/usr/sbin/wpa_supplicant", "-B", "-i", iface, "-c", str(config)], check=False, timeout=15)
    run_cmd(["/usr/sbin/dhcpcd", iface], check=False, timeout=20)
    time.sleep(3)
    return wifi_status(iface)


def scan_wifi(iface: str) -> dict[str, Any]:
    if iface not in WIFI_IFACES:
        raise ValueError(f"unknown wifi interface: {iface}")
    proc = run_cmd(["/sbin/iwlist", iface, "scan"], check=False, timeout=45)
    essids = []
    for line in proc.stdout.splitlines():
        match = re.search(r'ESSID:"(.*)"', line)
        if match:
            essid = match.group(1)
            if essid not in essids:
                essids.append(essid)
    return {"interface": iface, "essids": essids[:40], "matched_backup_ssids": [item for item in essids if item in {"WKRP", "My_MiFi_WiFi"}]}


def wifi_status(iface: str) -> dict[str, Any]:
    if iface not in WIFI_IFACES:
        raise ValueError(f"unknown wifi interface: {iface}")
    link = run_cmd(["/usr/sbin/ip", "-brief", "addr", "show", iface], check=False).stdout.splitlines()
    iwconfig = run_cmd(["/usr/sbin/iwconfig", iface], check=False).stdout.splitlines()
    wpa = run_cmd(["/usr/sbin/wpa_cli", "-i", iface, "status"], check=False).stdout.splitlines()
    return {"interface": iface, "ip_brief": link, "iwconfig": iwconfig, "wpa_status": wpa}


def usage() -> dict[str, Any]:
    return {
        "actions": [
            "status",
            "restart-dashboard",
            "restart-bots",
            "run-job <monitor|watchdog|joke|status|wifi-check>",
            "tail-log <activity|cron|dashboard|jarvis|kitt|net|router|start|wifi> [lines]",
            "show-crontab",
            "install-crontab",
            "install-file <wpa_wlan1|if_wlan1|rc_local>",
            "restart-wifi <wlan1>",
            "wifi-status <wlan1>",
            "scan-wifi <wlan1>",
        ]
    }


def main(argv: list[str]) -> int:
    ensure_root()
    if len(argv) < 2:
        return respond(True, "help", **usage())
    action = argv[1]
    try:
        if action == "help":
            payload = usage()
        elif action == "status":
            payload = monitor_status()
        elif action == "restart-dashboard":
            payload = restart_dashboard()
        elif action == "restart-bots":
            payload = restart_bots()
        elif action == "run-job":
            payload = run_job(argv[2])
        elif action == "tail-log":
            lines = int(argv[3]) if len(argv) > 3 else 40
            payload = tail_log(argv[2], lines)
        elif action == "show-crontab":
            payload = show_crontab()
        elif action == "install-crontab":
            payload = install_crontab()
        elif action == "install-file":
            payload = install_file(argv[2])
        elif action == "restart-wifi":
            payload = restart_wifi(argv[2])
        elif action == "wifi-status":
            payload = wifi_status(argv[2])
        elif action == "scan-wifi":
            payload = scan_wifi(argv[2])
        else:
            raise ValueError(f"unknown action: {action}")
        log_admin(action, argv[2:], True, message="", result=result_preview(payload))
        return respond(True, action, **payload)
    except Exception as exc:
        log_admin(action, argv[2:], False, str(exc), result={})
        return respond(False, action, error=str(exc))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
