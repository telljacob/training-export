"""
Automatic refreshes, for this user only, at each time the athlete picked. They run the helper without a window
("--refresh"), only while the athlete is signed in, and a time missed while the computer slept runs when it wakes.
No administrator rights and no password are needed.

- Windows: one scheduled task, "Training Export refresh".
- Mac: one launchd agent in ~/Library/LaunchAgents (launchd runs a calendar time missed in sleep at wake).
"""
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "Training Export refresh"
AGENT_LABEL = "local.training-export.refresh"
MAC = sys.platform == "darwin"


def command() -> tuple:
    """(program, arguments, working folder) that start a windowless refresh."""
    if getattr(sys, "frozen", False):                       # the packaged .exe, or the program inside the Mac .app
        exe = Path(sys.executable)
        return str(exe), "--refresh", str(exe.parent)
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    root = Path(__file__).resolve().parent.parent
    return str(pyw if pyw.exists() else exe), "-m training_export --refresh", str(root)


# -- Windows ---------------------------------------------------------------------------------------------------------
def task_xml(times: list, program: str, arguments: str, workdir: str) -> str:
    triggers = "".join(
        f"<CalendarTrigger><StartBoundary>2026-01-01T{t}:00</StartBoundary><Enabled>true</Enabled>"
        f"<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>" for t in times)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Downloads new Garmin files into your Training Export folder.</Description></RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT4H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(program)}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(workdir)}</WorkingDirectory></Exec></Actions>
</Task>
"""


def _schtasks(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _win_install(times: list) -> None:
    program, arguments, workdir = command()
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(path).write_text(task_xml(times, program, arguments, workdir), encoding="utf-16")
        r = _schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F")
        if r.returncode != 0:
            raise RuntimeError("Windows did not accept the refresh times: " + (r.stderr or r.stdout).strip()[:200])
    finally:
        os.unlink(path)


# -- Mac -------------------------------------------------------------------------------------------------------------
def agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def agent_plist(times: list, program: str, arguments: str, workdir: str) -> bytes:
    return plistlib.dumps({
        "Label": AGENT_LABEL,
        "ProgramArguments": [program, *arguments.split()],
        "WorkingDirectory": workdir,
        "StartCalendarInterval": [{"Hour": int(t[:2]), "Minute": int(t[3:])} for t in times],
        "ProcessType": "Background",
        "RunAtLoad": False,
    })


def _launchctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _mac_install(times: list) -> None:
    program, arguments, workdir = command()
    p = agent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"gui/{os.getuid()}/{AGENT_LABEL}")      # an older version of the times, if any
    p.write_bytes(agent_plist(times, program, arguments, workdir))
    r = _launchctl("bootstrap", f"gui/{os.getuid()}", str(p))
    if r.returncode != 0:
        raise RuntimeError("macOS did not accept the refresh times: " + (r.stderr or r.stdout).strip()[:200])


def _mac_remove() -> None:
    _launchctl("bootout", f"gui/{os.getuid()}/{AGENT_LABEL}")
    try:
        agent_path().unlink()
    except FileNotFoundError:
        pass


# -- either ----------------------------------------------------------------------------------------------------------
def install(times: list) -> None:
    _mac_install(times) if MAC else _win_install(times)


def remove() -> None:
    _mac_remove() if MAC else _schtasks("/Delete", "/TN", TASK_NAME, "/F")


def installed() -> bool:
    return agent_path().exists() if MAC else _schtasks("/Query", "/TN", TASK_NAME).returncode == 0
