import xml.etree.ElementTree as ET

import pytest

from jobsearch.delivery import scheduling

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _xml():
    return ET.fromstring(scheduling.build_task_xml(
        r'"C:\repo\.venv\Scripts\jobs.exe" run', r"C:\repo", "09:00:00"))


def test_task_runs_daily_at_nine():
    root = _xml()
    assert root.find(".//t:CalendarTrigger/t:StartBoundary", NS).text \
        .endswith("T09:00:00")
    assert root.find(".//t:ScheduleByDay/t:DaysInterval", NS).text == "1"


def test_task_wakes_the_computer():
    # A laptop asleep at 9am is the normal case, not the exception.
    assert _xml().find(".//t:Settings/t:WakeToRun", NS).text == "true"


def test_task_catches_up_after_a_missed_start():
    # Covers the nights the machine is shut down or wake timers are suppressed
    # on battery — the run happens at the next login instead of never.
    assert _xml().find(".//t:Settings/t:StartWhenAvailable", NS).text == "true"


def test_task_also_triggers_when_the_machine_wakes():
    """The trigger that makes this a daily agent on a laptop.

    Measured 2026-08-15: this machine has no S3 sleep, only Modern Standby, and
    on battery Windows hibernates it once the standby budget is spent — 06:12
    that morning, with the battery at 92%. A hibernating machine is electrically
    off, so WakeToRun cannot reach it and 09:00 was missed. StartWhenAvailable
    did not rescue it either: it re-checks missed runs after a BOOT, and a
    resume from hibernation is not a boot. Six hours after the machine came
    back, NumberOfMissedRuns was still 1 and nothing had run.

    Windows logs Power-Troubleshooter event 1 on every resume — it fired at
    09:56:28 that morning, six seconds after the machine came back. Subscribing
    to it is what makes "I opened my laptop" count as "power on".
    """
    subscription = _xml().find(".//t:EventTrigger/t:Subscription", NS).text
    assert "Microsoft-Windows-Power-Troubleshooter" in subscription
    assert "EventID=1" in subscription


def test_the_resume_trigger_waits_before_starting():
    # Wi-Fi and the Docker daemon are not up the instant the screen comes on.
    delay = _xml().find(".//t:EventTrigger/t:Delay", NS).text
    assert delay.startswith("PT") and delay != "PT0S"


def test_task_also_triggers_at_logon():
    # Covers a cold boot without waiting on Windows' own catch-up, which took
    # seven minutes on 2026-08-14.
    assert _xml().find(".//t:LogonTrigger/t:Enabled", NS).text == "true"


def test_the_logon_trigger_names_a_user():
    """A LogonTrigger with no UserId means ANY user logs on, which Windows
    treats as a privileged operation: schtasks rejected the first version of
    this task outright with "ERROR: Access is denied." Naming the account keeps
    registration possible without elevation."""
    user_id = _xml().find(".//t:LogonTrigger/t:UserId", NS)
    assert user_id is not None and user_id.text


def test_current_user_includes_the_domain(monkeypatch):
    monkeypatch.setenv("USERDOMAIN", "DVIR-LAPTOP")
    monkeypatch.setenv("USERNAME", "dvir")
    assert scheduling.current_user() == r"DVIR-LAPTOP\dvir"


def test_the_daily_trigger_survives_alongside_the_others():
    # The extra triggers are a safety net, not a replacement: 09:00 is still the
    # best case, and the only one that happens without the operator present.
    root = _xml()
    assert root.find(".//t:CalendarTrigger/t:StartBoundary", NS) is not None
    assert len(root.findall(".//t:Triggers/*", NS)) == 3


def test_task_runs_at_normal_priority():
    """Task Scheduler defaults to priority 7 (below normal), which throttles CPU
    and I/O. The agent's first act is a cold Node start plus MCP connections, and
    under that throttle it exceeded the SDK's 60s initialize timeout — the same
    work took seconds from a terminal. 4 is NORMAL_PRIORITY_CLASS."""
    assert _xml().find(".//t:Settings/t:Priority", NS).text == "4"


def test_task_runs_with_the_interactive_token():
    # InteractiveToken means no stored password. Waking from sleep keeps the
    # session logged on, so this is enough for the wake case.
    assert _xml().find(".//t:Principals/t:Principal/t:LogonType", NS).text \
        == "InteractiveToken"


def test_task_pins_the_command_and_working_directory():
    root = _xml()
    action = root.find(".//t:Actions/t:Exec", NS)
    assert action.find("t:Command", NS).text == r"C:\repo\.venv\Scripts\jobs.exe"
    assert action.find("t:Arguments", NS).text == "run"
    # A wrong working directory silently produces a task that fails every morning.
    assert action.find("t:WorkingDirectory", NS).text == r"C:\repo"


def test_battery_does_not_stop_the_task():
    root = _xml()
    assert root.find(".//t:Settings/t:DisallowStartIfOnBatteries", NS).text \
        == "false"
    assert root.find(".//t:Settings/t:StopIfGoingOnBatteries", NS).text == "false"


def test_a_path_with_an_ampersand_does_not_break_the_xml():
    # Program files paths are tame, but a repo under "R&D" would otherwise
    # produce XML that schtasks rejects as malformed.
    xml = scheduling.build_task_xml(r'"C:\R&D\jobs.exe" run', r"C:\R&D")
    root = ET.fromstring(xml)              # must parse at all
    assert root.find(".//t:Actions/t:Exec/t:WorkingDirectory", NS).text == r"C:\R&D"


def test_register_calls_schtasks_with_the_xml(monkeypatch):
    calls = {}

    class Result:
        returncode = 0
        stdout = "SUCCESS"
        stderr = ""

    def fake_run(args, **kwargs):
        calls["args"] = args
        with open(args[args.index("/XML") + 1], encoding="utf-16") as handle:
            calls["xml"] = handle.read()
        return Result()

    monkeypatch.setattr(scheduling.subprocess, "run", fake_run)
    scheduling.register(r'"C:\repo\.venv\Scripts\jobs.exe" run', r"C:\repo")
    assert calls["args"][0] == "schtasks"
    assert "/Create" in calls["args"] and "/F" in calls["args"]
    assert "WakeToRun" in calls["xml"]


def test_register_raises_with_schtasks_output_on_failure(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Access is denied."

    monkeypatch.setattr(scheduling.subprocess, "run",
                        lambda args, **kwargs: Result())
    with pytest.raises(RuntimeError) as excinfo:
        scheduling.register("cmd", "dir")
    assert "Access is denied" in str(excinfo.value)


def test_wake_timer_state_reports_both_power_sources(monkeypatch):
    class Result:
        returncode = 0
        stdout = ("Current AC Power Setting Index: 0x00000001\n"
                  "Current DC Power Setting Index: 0x00000002\n")
        stderr = ""

    monkeypatch.setattr(scheduling.subprocess, "run",
                        lambda args, **kwargs: Result())
    state = scheduling.wake_timer_state()
    assert "plugged in: enabled" in state
    # 2 = "important timers only", which silently suppresses this task.
    assert "will NOT wake" in state


def test_wake_timer_state_never_raises(monkeypatch):
    def boom(args, **kwargs):
        raise OSError("powercfg missing")

    monkeypatch.setattr(scheduling.subprocess, "run", boom)
    # A setup step reporting on power policy must not be able to fail setup.
    assert "power plan" in scheduling.wake_timer_state().lower()


def test_a_quoted_executable_path_containing_spaces_stays_intact():
    # Splitting on the first space would truncate any "C:\Program Files\..."
    # path into a command that does not exist.
    xml = scheduling.build_task_xml(
        r'"C:\Program Files\App\jobs.exe" run', r"C:\Program Files\App")
    action = ET.fromstring(xml).find(".//t:Actions/t:Exec", NS)
    assert action.find("t:Command", NS).text == r"C:\Program Files\App\jobs.exe"
    assert action.find("t:Arguments", NS).text == "run"
