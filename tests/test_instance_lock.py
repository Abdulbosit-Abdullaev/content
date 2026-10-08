import pytest

from contentbot.instance_lock import AlreadyRunning, InstanceLock


def test_second_instance_is_refused_until_the_first_stops(tmp_path):
    first = InstanceLock(tmp_path / "bot.lock")
    first.acquire()
    try:
        with pytest.raises(AlreadyRunning, match="already running"):
            InstanceLock(tmp_path / "bot.lock").acquire()
    finally:
        first.release()
    again = InstanceLock(tmp_path / "bot.lock")
    again.acquire()
    again.release()
