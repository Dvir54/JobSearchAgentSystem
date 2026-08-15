"""Register the scheduled task with Windows Task Scheduler.

Built from XML rather than `schtasks /Create /SC DAILY`, because neither the
settings that matter (WakeToRun, StartWhenAvailable) nor the extra triggers can
be expressed on the schtasks command line.

THREE triggers, because one moment a day is not enough on a laptop:

  09:00 daily   the best case, and the only one that needs nobody present.
  on resume     Power-Troubleshooter event 1, which Windows logs every time the
                machine comes back from sleep or hibernation.
  at logon      a cold boot, without waiting on Windows' own catch-up.

The resume trigger exists because of 2026-08-15. This machine has no S3 sleep,
only Modern Standby, so on battery Windows hibernates it once the standby budget
is spent — 06:12 that morning, at 92% charge. A hibernating machine is
electrically off, so WakeToRun could not reach it and 09:00 was missed.
StartWhenAvailable did not save it either: it re-checks missed runs after a
BOOT, and a resume from hibernation is not a boot. Six hours after the machine
came back, NumberOfMissedRuns was still 1 and nothing had run.

Extra triggers are only safe because `jobs run` skips a day it has already
finished — see db.successful_run_today. Without that guard, every lid-open would
mean another search and another email.
"""
import getpass
import os
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from jobsearch import config

# The wake-timer setting inside the Sleep subgroup of the active power plan.
_WAKE_TIMERS_GUID = "bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d"

_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Daily job search, CV tailoring, and digest email.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{start_time}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
    <EventTrigger>
      <Enabled>true</Enabled>
      <Delay>PT1M</Delay>
      <Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;&lt;Select Path="System"&gt;*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription>
    </EventTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT2M</Delay>
      <UserId>{user_id}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <WakeToRun>true</WakeToRun>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _split_command(command):
    """(executable, arguments) from a command line whose path may be quoted.

    Splitting on the first space would break any path containing one — which
    every `C:\\Program Files\\...` install has.
    """
    command = command.strip()
    if command.startswith('"'):
        closing = command.find('"', 1)
        return command[1:closing], command[closing + 1:].strip()
    executable, _, arguments = command.partition(" ")
    return executable, arguments.strip()


def current_user():
    """DOMAIN\\user for the account the task will run as."""
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def build_task_xml(command, working_dir, start_time=None, user_id=None):
    """`command` is the full command line; its first token is the executable."""
    executable, arguments = _split_command(command)
    # Escaped, not interpolated raw: a repo path containing & or < would
    # otherwise produce XML that schtasks rejects as malformed.
    return _TASK_XML.format(
        start_time=escape(start_time or config.SCHEDULE_TIME),
        command=escape(executable), arguments=escape(arguments),
        working_dir=escape(working_dir),
        user_id=escape(user_id or current_user()))


def register(command, working_dir, task_name=None):
    """Create or replace the scheduled task. Raises with schtasks' own output."""
    name = task_name or config.TASK_NAME
    xml = build_task_xml(command, working_dir)
    # schtasks /XML requires UTF-16; a UTF-8 file is rejected with a misleading
    # "The task XML is malformed".
    path = Path(tempfile.gettempdir()) / f"{name}.xml"
    path.write_text(xml, encoding="utf-16")
    result = subprocess.run(
        ["schtasks", "/Create", "/TN", name, "/XML", str(path), "/F"],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks could not register {name!r}: "
            f"{(result.stderr or result.stdout).strip()}")


def wake_timer_state():
    """A readable line about whether the power plan will honour WakeToRun.

    Reported, never changed: a laptop waking itself in a closed bag is the
    operator's call, not this program's. Values are 0=disabled, 1=enabled,
    2=important wake timers only (which suppresses this task).

    Never raises — a setup step that only reports on power policy must not be
    able to fail the setup.
    """
    labels = {0: "disabled (this task will NOT wake the machine)",
              1: "enabled",
              2: "important timers only (this task will NOT wake the machine)"}
    try:
        result = subprocess.run(
            ["powercfg", "/q", "SCHEME_CURRENT", "SUB_SLEEP", _WAKE_TIMERS_GUID],
            capture_output=True, text=True)
    except OSError as exc:
        return f"Could not read the power plan ({exc})."
    if result.returncode != 0:
        return "Could not read the power plan; check wake timers manually."

    values = {}
    for line in result.stdout.splitlines():
        if "AC Power Setting Index" in line:
            values["plugged in"] = int(line.split(":")[-1].strip(), 16)
        elif "DC Power Setting Index" in line:
            values["on battery"] = int(line.split(":")[-1].strip(), 16)
    if not values:
        return "Could not read the power plan wake-timer setting; check it manually."
    return "Wake timers — " + ", ".join(
        f"{where}: {labels.get(value, value)}" for where, value in values.items())
