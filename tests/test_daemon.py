import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xmpp_transport.runtime.daemon import (
    DaemonError,
    daemon_status,
    read_pid_file,
    remove_pid_file,
    stop_daemon,
)


class DaemonPidFileTests(unittest.TestCase):
    def test_missing_pid_file_reports_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "missing.pid")
            self.assertEqual((False, None), daemon_status(path))

    def test_invalid_pid_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transport.pid"
            path.write_text("not-a-pid\n", encoding="utf-8")
            with self.assertRaisesRegex(DaemonError, "Invalid pid file"):
                read_pid_file(str(path))

    def test_remove_pid_file_checks_expected_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transport.pid"
            path.write_text("123\n", encoding="utf-8")
            remove_pid_file(str(path), expected_pid=456)
            self.assertTrue(path.exists())
            remove_pid_file(str(path), expected_pid=123)
            self.assertFalse(path.exists())

    def test_stop_removes_stale_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transport.pid"
            path.write_text("123\n", encoding="utf-8")
            with patch(
                "xmpp_transport.runtime.daemon.is_process_running", return_value=False
            ):
                with self.assertRaisesRegex(DaemonError, "Removed stale pid file"):
                    stop_daemon(str(path))
            self.assertFalse(path.exists())

    def test_current_process_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transport.pid"
            path.write_text(f"{os.getpid()}\n", encoding="utf-8")
            self.assertEqual((True, os.getpid()), daemon_status(str(path)))


if __name__ == "__main__":
    unittest.main()
