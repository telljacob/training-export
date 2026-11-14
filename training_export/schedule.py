"""
Automatic refreshes: one Windows scheduled task for this user, with a trigger at each time the athlete picked.
It runs the helper without a window ("--refresh"), only while they are signed in, and catches up a missed time when the
computer wakes. No administrator rights and no password are needed.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "Training Export refresh"


def command() -> tuple:
    """(program, arguments, working folder) that start a windowless refresh."""
    if getattr(sys, "frozen", False):                       # the packaged .exe
        exe = Path(sys.executable)
        return str(exe), "--refresh", str(exe.parent)
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    root = Path(__file__).resolve().parent.parent
    return str(pyw if pyw.exists() else exe), "-m training_export --refresh", str(root)


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


def install(times: list) -> None:
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


def remove() -> None:
    _schtasks("/Delete", "/TN", TASK_NAME, "/F")


def installed() -> bool:
    return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0
