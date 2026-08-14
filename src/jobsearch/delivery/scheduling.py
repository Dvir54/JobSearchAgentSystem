"""Register the daily 09:00 task with Windows Task Scheduler.

Built from XML rather than `schtasks /Create /SC DAILY`, because the two settings
that matter most cannot be expressed on the schtasks command line: WakeToRun
(wake a sleeping laptop) and StartWhenAvailable (run late rather than never).

Neither covers a machine that is shut down or hibernated — a timer cannot power on
a machine that is off — which is exactly why both are set. Plugged in and asleep,
the run happens at 09:00; otherwise it happens at the next login.
"""
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


def build_task_xml(command, working_dir, start_time=None):
    """`command` is the full command line; its first token is the executable."""
    executable, arguments = _split_command(command)
    # Escaped, not interpolated raw: a repo path containing & or < would
    # otherwise produce XML that schtasks rejects as malformed.
    return _TASK_XML.format(
        start_time=escape(start_time or config.SCHEDULE_TIME),
        command=escape(executable), arguments=escape(arguments),
        working_dir=escape(working_dir))


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
