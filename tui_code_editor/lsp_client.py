"""Minimal LSP client skeleton that launches a language server and exchanges JSON-RPC messages.

This is intentionally small: it demonstrates process management and framing messages with Content-Length headers.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from asyncio.subprocess import PIPE
from typing import Optional, Dict, Any, Callable


class SimpleLSPClient:
    """Minimal but featureful LSP client.

    Features:
    - Spawns language server process
    - Sends requests and notifications
    - Tracks pending requests by id and resolves futures
    - Exposes on_diagnostics callback for publishDiagnostics
    - Exposes on_notification handler registration
    """

    def __init__(self, cmd: list[str]):
        self.cmd = cmd
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self.on_diagnostics: Optional[Callable[[dict], None]] = None
        self._notification_handlers: Dict[str, Callable[[dict], None]] = {}

    async def start(self):
        if shutil.which(self.cmd[0]) is None:
            raise FileNotFoundError(f"Language server binary not found: {self.cmd[0]}")
        self.proc = await asyncio.create_subprocess_exec(*self.cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE)
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())
        # send initialize as notification is often required by some servers
        await asyncio.sleep(0.1)

    async def _read_stdout(self):
        assert self.proc and self.proc.stdout
        while True:
            header = await self.proc.stdout.readuntil(b"\r\n\r\n")
            # parse Content-Length
            headers = header.decode().split("\r\n")
            length = 0
            for h in headers:
                if h.lower().startswith("content-length"):
                    length = int(h.split(":", 1)[1].strip())
            if length <= 0:
                continue
            body = await self.proc.stdout.readexactly(length)
            try:
                msg = json.loads(body.decode())
            except Exception:
                continue
            # If it's a response to a request (has id), resolve future
            if isinstance(msg, dict):
                if "id" in msg:
                    rid = msg.get("id")
                    fut = self._pending.pop(rid, None)
                    if fut and not fut.done():
                        fut.set_result(msg.get("result"))
                # Notifications or requests from server (no id)
                elif "method" in msg:
                    method = msg["method"]
                    params = msg.get("params")
                    # diagnostics
                    if method == "textDocument/publishDiagnostics":
                        if callable(self.on_diagnostics):
                            try:
                                self.on_diagnostics(params)
                            except Exception:
                                pass
                    # call notification handlers
                    handler = self._notification_handlers.get(method)
                    if handler:
                        try:
                            handler(params)
                        except Exception:
                            pass
            # For debugging
            # print("LSP RECV:", msg)

    async def _read_stderr(self):
        assert self.proc and self.proc.stderr
        while True:
            data = await self.proc.stderr.readline()
            if not data:
                break
            sys.stderr.write(data.decode())

    async def send_notification(self, method: str, params: dict = None):
        if params is None:
            params = {}
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._write(msg)

    async def send_request(self, method: str, params: dict = None, timeout: float = 5.0) -> Any:
        if params is None:
            params = {}
        self._id += 1
        mid = self._id
        msg = {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}
        fut = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._write(msg)
        try:
            res = await asyncio.wait_for(fut, timeout=timeout)
            return res
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise

    async def _write(self, msg: dict):
        encoded = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("utf-8")
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(header + encoded)
        await self.proc.stdin.drain()

    def register_notification_handler(self, method: str, handler: Callable[[dict], None]):
        self._notification_handlers[method] = handler

    async def stop(self):
        if not self.proc:
            return
        self.proc.terminate()
        await self.proc.wait()


async def demo_run_server():
    # Example: try to start pylsp if available
    client = SimpleLSPClient(["pylsp"])  # depends on python-lsp-server installed system-wide
    try:
        await client.start()
        await client.send("initialize", {"capabilities": {}}, is_notification=False)
        # Keep running briefly to show messages
        await asyncio.sleep(2)
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(demo_run_server())
