"""POSIX daemon lifecycle helpers.

This follows the lifecycle used by the original xmpp-transport-max service:
double-fork, a PID file, SIGTERM shutdown, and stale PID-file cleanup.
"""

import atexit
import errno
import os
import signal
import sys
import time
from typing import Optional


class DaemonError(RuntimeError):
    pass


def daemonize(
    pid_file: str,
    working_directory: Optional[str] = None,
    umask: int = 0o027,
) -> None:
    if os.name != "posix":
        raise DaemonError("Daemon mode is supported only on POSIX systems")
    _ensure_not_running(pid_file)
    _fork_parent_exit()
    os.setsid()
    _fork_parent_exit()
    if working_directory:
        os.chdir(working_directory)
    os.umask(umask)
    _redirect_standard_fds()
    _write_pid_file(pid_file)
    atexit.register(remove_pid_file, pid_file, os.getpid())


def stop_daemon(pid_file: str, timeout: float = 30.0) -> None:
    pid = read_pid_file(pid_file)
    if pid is None:
        raise DaemonError(f"No pid file found at {pid_file}")
    if not is_process_running(pid):
        remove_pid_file(pid_file)
        raise DaemonError(f"Removed stale pid file for stopped process {pid}")

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_running(pid):
            remove_pid_file(pid_file)
            return
        time.sleep(0.2)
    raise DaemonError(f"Process {pid} did not stop within {timeout:g} seconds")


def daemon_status(pid_file: str) -> tuple[bool, Optional[int]]:
    pid = read_pid_file(pid_file)
    if pid is None:
        return False, None
    return is_process_running(pid), pid


def read_pid_file(pid_file: str) -> Optional[int]:
    try:
        with open(pid_file, encoding="utf-8") as file:
            value = file.read().strip()
    except FileNotFoundError:
        return None
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise DaemonError(f"Invalid pid file {pid_file}: {value!r}") from exc


def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def remove_pid_file(pid_file: str, expected_pid: Optional[int] = None) -> None:
    if expected_pid is not None:
        current_pid = read_pid_file(pid_file)
        if current_pid != expected_pid:
            return
    try:
        os.unlink(pid_file)
    except FileNotFoundError:
        pass


def _ensure_not_running(pid_file: str) -> None:
    pid = read_pid_file(pid_file)
    if pid is None:
        return
    if is_process_running(pid):
        raise DaemonError(f"Transport is already running as pid {pid}")
    remove_pid_file(pid_file)


def _fork_parent_exit() -> None:
    try:
        pid = os.fork()
    except OSError as exc:
        raise DaemonError(f"Could not fork daemon process: {exc}") from exc
    if pid > 0:
        os._exit(0)


def _redirect_standard_fds() -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    fd = os.open(os.devnull, os.O_RDWR)
    try:
        for target_fd in (0, 1, 2):
            os.dup2(fd, target_fd)
    finally:
        if fd > 2:
            os.close(fd)


def _write_pid_file(pid_file: str) -> None:
    directory = os.path.dirname(pid_file)
    if directory:
        os.makedirs(directory, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(pid_file, flags, 0o644)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise DaemonError(f"Pid file already exists at {pid_file}") from exc
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(f"{os.getpid()}\n")
