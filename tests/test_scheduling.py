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
