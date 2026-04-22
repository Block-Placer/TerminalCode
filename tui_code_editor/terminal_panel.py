"""Terminal panel with PTY support for interactive shells and command execution."""
from __future__ import annotations

import asyncio
import os
import pty
import select
import tty
from typing import Optional, Callable


class TerminalPanel:
    """TerminalPanel supports both one-shot runs and persistent PTY sessions.

    Methods:
    - run(cmd): run command and return output when finished
    - start_session(cmd, on_output): start persistent PTY session and call on_output(str) with incremental output
    - write_input(data): write bytes/str to PTY
    - stop_session(): stop PTY session
    """

    def __init__(self):
        self.output = ""
        self._master_fd: Optional[int] = None
        self._proc_pid: Optional[int] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._on_output: Optional[Callable[[str], None]] = None

    async def run(self, cmd: str, timeout: float = 10.0) -> str:
        # run command via /bin/sh -c inside a spawned pty so interactive programs work
        master, slave = pty.openpty()
        pid = os.fork()
        if pid == 0:
            # child
            os.setsid()
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            try:
                tty.setraw(0)
            except Exception:
                pass
            os.execvp('/bin/sh', ['/bin/sh', '-c', cmd])
        else:
            # parent
            os.close(slave)
            output = b""
            try:
                # read until process exits or timeout
                while True:
                    r, _, _ = select.select([master], [], [], 0.1)
                    if master in r:
                        data = os.read(master, 4096)
                        if not data:
                            break
                        output += data
                    # check if child exited
                    try:
                        pid_done, status = os.waitpid(pid, os.WNOHANG)
                        if pid_done == pid:
                            break
                    except ChildProcessError:
                        break
            finally:
                try:
                    os.close(master)
                except Exception:
                    pass
            try:
                self.output = output.decode('utf-8', errors='replace')
            except Exception:
                self.output = str(output)
            return self.output

    def start_session(self, cmd: str, on_output: Optional[Callable[[str], None]] = None) -> None:
        """Start a persistent PTY session. on_output will be called with incremental output."""
        if self._master_fd is not None:
            # already running
            return
        master, slave = pty.openpty()
        pid = os.fork()
        if pid == 0:
            # child
            os.setsid()
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            try:
                tty.setraw(0)
            except Exception:
                pass
            os.execvp('/bin/sh', ['/bin/sh', '-c', cmd])
        else:
            os.close(slave)
            self._master_fd = master
            self._proc_pid = pid
            self._on_output = on_output
            loop = asyncio.get_event_loop()
            self._reader_task = loop.create_task(self._reader_loop())

    async def _reader_loop(self):
        master = self._master_fd
        if master is None:
            return
        try:
            while True:
                await asyncio.sleep(0.05)
                r, _, _ = select.select([master], [], [], 0)
                if master in r:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    try:
                        text = data.decode('utf-8', errors='replace')
                    except Exception:
                        text = str(data)
                    self.output += text
                    if self._on_output:
                        try:
                            self._on_output(self.output)
                        except Exception:
                            pass
                # check if child exited
                try:
                    pid_done, status = os.waitpid(self._proc_pid or -1, os.WNOHANG)
                    if pid_done and pid_done == self._proc_pid:
                        break
                except ChildProcessError:
                    break
        finally:
            try:
                os.close(master)
            except Exception:
                pass
            self._master_fd = None
            self._proc_pid = None
            self._reader_task = None

    def write_input(self, data: str) -> None:
        if self._master_fd is None:
            return
        try:
            os.write(self._master_fd, data.encode('utf-8'))
        except Exception:
            pass

    def stop_session(self) -> None:
        try:
            if self._proc_pid:
                try:
                    os.kill(self._proc_pid, 15)
                except Exception:
                    pass
        finally:
            self._proc_pid = None
            self._master_fd = None
