"""Socket, process and summary helpers for the DEV checks. Carried from LastDB's scripts/harness.py."""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import time

SOCKET_OPERATIONS = {
    "/health": "health", "/api/status": "status", "/api/system/boot-identity": "boot_identity",
    "/api/db/native-status": "native_status", "/api/queries/batch": "query_batch",
    "/api/mutations/batch": "mutation_batch",
}
SOCKET_REFUSALS = frozenset({
    "cas_conflict", "compaction_behind", "field_value_too_large", "persist_queue_full",
    "read_resource_limit", "storage_full", "write_resource_limit",
})


class UnixConnection(http.client.HTTPConnection):
    """HTTP/1.1 client connection over an AF_UNIX stream socket."""

    def __init__(self, path, timeout=30):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def classify_socket_refusal(path, response, *, decode_json_fn=json.loads, ignored_decode_errors=(ValueError,)):
    refusal = {"operation": SOCKET_OPERATIONS.get(path, "other"), "status": response.status, "reason": "unrecognized"}
    try:
        raw = response.read(4097)
    except (OSError, http.client.HTTPException):
        return refusal
    if len(raw) > 4096:
        return refusal
    if raw.startswith(b"node is busy: too many concurrent reads; retry after "):
        refusal["reason"] = "read_busy"
        return refusal
    try:
        body = decode_json_fn(raw)
    except ignored_decode_errors:
        return refusal
    code = body.get("error") if isinstance(body, dict) else None
    if isinstance(code, str) and code in SOCKET_REFUSALS:
        refusal["reason"] = code
    return refusal


def verify_shutdown_receipt(receipt, pid, start_ts=None):
    if not isinstance(receipt, dict) or receipt.get("pid") != pid or receipt.get("flush_ok") is not True:
        return False
    if "version" in receipt and receipt.get("version") != 1:
        return False
    return start_ts is None or receipt.get("start_ts") == start_ts


def stop_process(process, *, timeout=45, kill_timeout=10, crash=False):
    forced = False
    if process.poll() is None:
        process.send_signal(signal.SIGKILL if crash else signal.SIGTERM)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            forced = True
            process.kill()
            process.wait(timeout=kill_timeout)
    return forced


def poll_until(timeout_seconds, step, *, interval=0.05, timeout_error="timed out"):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = step()
        if result:
            return result
        time.sleep(interval)
    if callable(timeout_error):
        raise timeout_error()
    raise timeout_error if isinstance(timeout_error, BaseException) else RuntimeError(timeout_error)


def append_step_summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(text)


def summary_line(line, *, echo=True):
    if echo:
        print(line, flush=True)
    append_step_summary(line + "\n")


def write_private(path, payload):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
