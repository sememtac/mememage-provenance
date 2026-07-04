"""Install Mememage as a user service so the mint server runs on every login.

macOS uses a LaunchAgent at `~/Library/LaunchAgents/com.mememage.mint.plist`.
Linux uses a systemd-user unit at `~/.config/systemd/user/mememage-mint.service`.

After install, the dashboard is permanently available at the configured
host/port and the user no longer needs to touch the terminal — they bookmark
the dashboard URL and that's their entire interface.
"""

import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
import urllib.error
import ssl
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # contains Payload/, docs/, etc.

PLIST_PATH = Path("~/Library/LaunchAgents/com.mememage.mint.plist").expanduser()
PLIST_LABEL = "com.mememage.mint"

SYSTEMD_UNIT_PATH = Path("~/.config/systemd/user/mememage-mint.service").expanduser()
SYSTEMD_UNIT_NAME = "mememage-mint.service"

LOG_PATH = Path("~/.mememage/server.log").expanduser()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def _plist_template(python_bin: str, port: int, log_path: Path, working_dir: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>-m</string>
        <string>mememage.server</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>WorkingDirectory</key>
    <string>{working_dir}</string>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


def _systemd_template(python_bin: str, port: int, working_dir: Path) -> str:
    return f"""[Unit]
Description=Mememage Mint Server
After=network.target

[Service]
Type=simple
WorkingDirectory={working_dir}
ExecStart={python_bin} -m mememage.server
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _probe_health(port: int, attempts: int = 6, delay: float = 1.0) -> str | None:
    """Probe /health on https:// then http://; returns the working base URL or None."""
    for _ in range(attempts):
        for scheme in ("https", "http"):
            url = f"{scheme}://localhost:{port}/health"
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(url, timeout=2, context=ctx) as r:
                    if r.status == 200:
                        return f"{scheme}://localhost:{port}"
            except Exception:
                pass
        time.sleep(delay)
    return None


# ---------------------------------------------------------------------------
# macOS — LaunchAgent
# ---------------------------------------------------------------------------

def _install_macos(port: int) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = _plist_template(sys.executable, port, LOG_PATH, PROJECT_ROOT)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)

    replacing = PLIST_PATH.exists()
    if replacing:
        # Unload first so the new plist takes effect cleanly.
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        )

    PLIST_PATH.write_text(plist)
    verb = "Replaced" if replacing else "Wrote"
    print(f"{verb} LaunchAgent at {PLIST_PATH}")

    result = subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        stderr=subprocess.PIPE, text=True,
    )
    if result.returncode != 0:
        print(f"\u2717 launchctl load failed (exit {result.returncode}):")
        print(f"   {result.stderr.strip()}")
        print(f"   Try: launchctl load {PLIST_PATH}")
        sys.exit(1)
    print("Loaded the LaunchAgent.")


def _uninstall_macos() -> None:
    if not PLIST_PATH.exists():
        print("Not installed (no LaunchAgent plist found).")
        return
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
    )
    PLIST_PATH.unlink()
    print(f"Removed {PLIST_PATH}")
    print("Mint server stopped and uninstalled.")


def _status_macos() -> bool:
    """Returns True if the LaunchAgent is loaded."""
    if not PLIST_PATH.exists():
        print(f"  LaunchAgent plist: absent")
        return False
    print(f"  LaunchAgent plist: {PLIST_PATH}")
    result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    loaded = PLIST_LABEL in result.stdout
    print(f"  Loaded:            {'yes' if loaded else 'no'}")
    return loaded


# ---------------------------------------------------------------------------
# Linux — systemd user unit
# ---------------------------------------------------------------------------

def _install_linux(port: int) -> None:
    SYSTEMD_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    unit = _systemd_template(sys.executable, port, PROJECT_ROOT)
    replacing = SYSTEMD_UNIT_PATH.exists()
    SYSTEMD_UNIT_PATH.write_text(unit)
    verb = "Replaced" if replacing else "Wrote"
    print(f"{verb} systemd unit at {SYSTEMD_UNIT_PATH}")

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", SYSTEMD_UNIT_NAME],
        ["systemctl", "--user", "restart", SYSTEMD_UNIT_NAME],
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"\u2717 `{' '.join(cmd)}` failed:")
            print(f"   {result.stderr.strip()}")
            sys.exit(1)
    print("Loaded the systemd unit.")


def _uninstall_linux() -> None:
    subprocess.run(["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME],
                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    subprocess.run(["systemctl", "--user", "disable", SYSTEMD_UNIT_NAME],
                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    if SYSTEMD_UNIT_PATH.exists():
        SYSTEMD_UNIT_PATH.unlink()
        print(f"Removed {SYSTEMD_UNIT_PATH}")
    subprocess.run(["systemctl", "--user", "daemon-reload"],
                   stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    print("Mint server stopped and uninstalled.")


def _status_linux() -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME],
        capture_output=True, text=True,
    )
    active = result.stdout.strip()
    print(f"  systemd unit:      {SYSTEMD_UNIT_PATH}")
    print(f"  Status:            {active}")
    return active == "active"


# ---------------------------------------------------------------------------
# Public entry points (called from __main__.py)
# ---------------------------------------------------------------------------

def _persist_port_to_config(port: int) -> None:
    """Write the chosen port into ~/.mememage/server.json so it's the single,
    dashboard-editable source of truth. The service unit no longer hardcodes
    --port, so changing the port is a config edit + restart (no unit surgery)."""
    cfg_path = Path("~/.mememage/server.json").expanduser()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data["port"] = int(port)
    cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(str(cfg_path), 0o600)
    except OSError:
        pass


def install(port: int = 8443) -> None:
    # Port lives in server.json now (config-driven), not baked into the unit.
    _persist_port_to_config(port)
    system = platform.system()
    if system == "Darwin":
        _install_macos(port)
    elif system == "Linux":
        _install_linux(port)
    else:
        print(f"Automatic install is not yet supported on {system}.")
        print("Run `mememage serve` manually, or contribute a service template")
        print("for your platform.")
        sys.exit(1)

    print()
    print("Waiting for server to come up\u2026")
    base = _probe_health(port)
    if base:
        print(f"\u2713 Server reachable at {base}")
        print(f"  Dashboard: {base}/dashboard")
        print()
        print("  The server will start automatically on every login.")
        print("  Bookmark the dashboard URL above; you won't need the terminal again.")
    else:
        print("\u26a0 Server didn't respond on the expected port within the probe window.")
        print(f"  Check the log:  tail -f {LOG_PATH}")
        print(f"  Or:             mememage status")


def uninstall() -> None:
    system = platform.system()
    if system == "Darwin":
        _uninstall_macos()
    elif system == "Linux":
        _uninstall_linux()
    else:
        print(f"Automatic uninstall is not yet supported on {system}.")
        sys.exit(1)


def status(port: int = 8443) -> None:
    print("Mint server status:")
    system = platform.system()
    if system == "Darwin":
        _status_macos()
    elif system == "Linux":
        _status_linux()
    else:
        print(f"  Platform: {system} (no service template)")

    base = _probe_health(port, attempts=2, delay=0.5)
    if base:
        print(f"  Reachable:         yes ({base})")
        print(f"  Dashboard:         {base}/dashboard")
    else:
        print(f"  Reachable:         no (port {port})")
        print()
        print(f"  Tail the log to investigate:  tail -f {LOG_PATH}")
