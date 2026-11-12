"""Desktop notification at a gate, DONE and on error: a Windows toast through PowerShell 5.1's
WinRT ToastNotificationManager (no extra modules). Fire-and-forget: the PowerShell process is
started detached and never waited for; any failure, a non-Windows system, `[notify] toast =
false` or env FRAMEGATE_TOAST=0 make it a silent no-op. It never blocks or breaks a run."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from framegate.config import Config

#: AppUserModelID of Windows PowerShell: registered on every Windows 10/11, so the toast shows
#: without registering an own app id (it appears as "Windows PowerShell" in the action center)
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

#: title/text come in via environment variables: no quoting of user text on a command line
SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
    "ContentType=WindowsRuntime];"
    "[void][Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,"
    "ContentType=WindowsRuntime];"
    "$t=[Security.SecurityElement]::Escape($env:FRAMEGATE_TOAST_TITLE);"
    "$b=[Security.SecurityElement]::Escape($env:FRAMEGATE_TOAST_TEXT);"
    "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
    "$x.LoadXml(\"<toast><visual><binding template='ToastGeneric'><text>$t</text>"
    '<text>$b</text></binding></visual></toast>");'
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "$env:FRAMEGATE_TOAST_APPID).Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)


def enabled(cfg: Config) -> bool:
    if os.environ.get("FRAMEGATE_TOAST", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return cfg.toast and sys.platform == "win32"


def command() -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SCRIPT]


def _spawn(cmd: list[str], **kw: Any) -> subprocess.Popen[bytes]:
    return subprocess.Popen(cmd, **kw)


def toast(cfg: Config, title: str, text: str) -> subprocess.Popen[bytes] | None:
    """Show a toast; returns the started process (None = not shown: disabled or failed)."""
    if not enabled(cfg):
        return None
    env = dict(
        os.environ,
        FRAMEGATE_TOAST_TITLE=title[:120],
        FRAMEGATE_TOAST_TEXT=text[:300],
        FRAMEGATE_TOAST_APPID=APP_ID,
    )
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    # break away from a kill-on-close job of the caller (e.g. a background task runner), so the
    # toast survives framegate's exit; a job that forbids it -> start without
    for flags in (no_window | breakaway, no_window):
        try:
            return _spawn(
                command(),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except Exception:  # never let a notification break the run
            continue
    return None


def settle(proc: subprocess.Popen[bytes] | None, timeout: float = 5.0) -> None:
    """At the end of a run (the process exits next): give the toast up to `timeout` seconds to
    show, in case the process dies with ours. Never raises."""
    if proc is None:
        return
    try:
        proc.wait(timeout)
    except Exception:
        pass
