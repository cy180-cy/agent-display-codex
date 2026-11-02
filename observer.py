"""Bounded, read-only observer for one explicitly selected Codex rollout.

No Codex RPC calls, database access, model calls, command execution, or writes to
the observed directory. The rollout format is an internal, versioned source;
unrecognized events are ignored and never interpreted as instructions.
"""
from __future__ import annotations

import collections
import ctypes
import datetime as dt
import gc
import json
import os
import re
import threading
import time
from pathlib import Path

MAX_LINE = 512 * 1024
READ_CHUNK = 64 * 1024
INCREMENT_BUDGET = 2 * 1024 * 1024
CATEGORIES = {
    "read": "读取资料", "write": "修改文件", "search": "搜索检索",
    "command": "执行命令", "browser": "浏览器", "plan": "任务编排",
    "check": "验证检查", "tool": "其他工具",
}


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 1e11 else float(value)
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def clean_text(value, limit=260):
    if isinstance(value, list):
        value = "\n".join(str(part.get("text", "")) for part in value if isinstance(part, dict) and str(part.get("type", "")).lower() in ("text", "output_text", "input_text"))
    elif isinstance(value, dict):
        value = value.get("text", "")
    text = str(value or "")
    text = re.sub(r"<oai-mem-citation>[\s\S]*?</oai-mem-citation>", "", text)
    text = re.sub(r"(?i)\b(?:sk-|sess-)[a-z0-9_-]{12,}", "[凭据已隐藏]", text)
    text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+", r"\1[已隐藏]", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text[:limit] + ("…" if len(text) > limit else "")


def category(name, arguments=""):
    """Classify a recorded action; never execute or expose its arguments."""
    name, hint = str(name).lower(), str(arguments)[:16000].lower()
    both = name + " " + hint
    if any(x in name for x in ("apply_patch", "filechange", "write_file", "edit_file")):
        return "write"
    if any(x in name for x in ("browser", "cua", "web__", "web.run", "web_search")):
        return "browser" if "cua" in name or "browser" in name else "search"
    if any(x in name for x in ("plan", "collab", "agent", "thread", "wait")):
        return "plan"
    if any(x in both for x in ("pytest", "unittest", "npm test", "--check", "validateonly", "validate-only")):
        return "check"
    if any(x in hint for x in ("get-content", "read_text(", "read_bytes(", "test-path")):
        return "read"
    if re.search(r"(?:^|[\s;\"'])rg\s|select-string|search_query", hint):
        return "search"
    if any(x in name for x in ("exec", "command", "shell", "process")):
        return "command"
    return "tool"


def result_status(item):
    value = str(item.get("status", "")).lower()
    exit_code = item.get("exit_code", item.get("exitCode"))
    if value in ("failed", "error", "declined", "rejected", "cancelled", "canceled") or isinstance(exit_code, int) and exit_code != 0:
        return "failed"
    if value in ("completed", "complete", "succeeded", "success") or exit_code == 0:
        return "complete"
    return "returned"


class LineDecoder:
    """Cap memory even when a screenshot/tool result spans megabytes."""
    def __init__(self, consume, skip_first=False):
        self.consume = consume
        self.buffer = b""
        self.discard = skip_first
        self.skipped = 0

    def feed(self, data):
        newline_count = data.count(b"\n")
        for i, part in enumerate(data.split(b"\n")):
            ended = i < newline_count
            if not self.discard:
                if len(self.buffer) + len(part) <= MAX_LINE:
                    self.buffer += part
                else:
                    self.buffer = b""
                    self.discard = True
                    self.skipped += 1
            if ended:
                if not self.discard and self.buffer:
                    try:
                        record = json.loads(self.buffer)
                        if isinstance(record, dict):
                            self.consume(record)
                    except (ValueError, UnicodeError):
                        self.skipped += 1
                self.buffer = b""
                self.discard = False


class Turn:
    def __init__(self, turn_id, stamp):
        self.id = str(turn_id)
        self.started = stamp
        self.ended = None
        self.status = "unknown"
        self.steps = collections.OrderedDict()
        self.messages = collections.deque(maxlen=10)
        self.summary = ""
        self.observed = 0
        self.pruned = 0


class Observer:
    def __init__(self, config):
        self.config = config
        self.path = Path(config["rollout_path"]).resolve()
        self.thread_id = config["thread_id"]
        if not self.path.name.endswith(self.thread_id + ".jsonl"):
            raise ValueError("配置的记录文件与聊天标识不匹配")
        self.max_steps = min(240, max(20, int(config.get("max_steps_per_turn", 160))))
        self.max_turns = min(6, max(1, int(config.get("max_turns", 4))))
        self.tail_bytes = min(16 * 1024 * 1024, max(1024, int(config.get("tail_bytes", 8 * 1024 * 1024))))
        self.turns = collections.OrderedDict()
        self.current_id = "recent"
        self.offset = 0
        self.identity = None
        self.initialized = False
        self.partial_history = False
        self.decoder = LineDecoder(self.consume)
        self.lock = threading.RLock()
        self.changed = 0
        self.last_record = None
        self.last_check = None
        self.error = None
        self.source_bytes = 0
        self.read_bytes = 0
        self.scanning = False
        self.closed = threading.Event()
        self.last_cleanup = None

    def clear_history(self):
        """Drop viewer history only; preserve the reader position and active work."""
        with self.lock:
            before = process_memory()
            total = sum(len(turn.steps) for turn in self.turns.values())
            old_turns = len(self.turns)
            latest = next(reversed(self.turns.values()), None)
            if latest is not None:
                # Pending requests need their IDs so a later result can match.
                recent_ids = {step["id"] for step in list(latest.steps.values())[-16:]}
                keep = collections.OrderedDict((key, step) for key, step in latest.steps.items()
                                               if key in recent_ids or step["status"] == "active")
                latest.pruned += len(latest.steps) - len(keep)
                latest.steps = keep
                latest.messages = collections.deque(list(latest.messages)[-1:], maxlen=10)
                self.turns = collections.OrderedDict([(latest.id, latest)])
                self.current_id = latest.id
            retained = sum(len(turn.steps) for turn in self.turns.values())
            self.partial_history = True
            self.changed += 1
            # Keep the decoder intact: a record may currently be half-read.
            gc.collect()
            self.last_cleanup = {"at": time.time(), "removedSteps": total - retained,
                                 "removedTurns": max(0, old_turns - len(self.turns)),
                                 "retainedSteps": retained, "before": before,
                                 "after": process_memory(), "scope": "viewer-cache-only"}
            return dict(self.last_cleanup)

    def turn(self, turn_id=None, stamp=None):
        turn_id = str(turn_id or self.current_id or "recent")
        if turn_id not in self.turns:
            self.turns[turn_id] = Turn(turn_id, stamp)
            # A bounded tail may begin before its first explicit turn identity.
            # Reattach only the unnamed tail, never merge two identified turns.
            if turn_id != "recent" and self.current_id == "recent" and "recent" in self.turns:
                old = self.turns.pop("recent")
                target = self.turns[turn_id]
                target.steps, target.messages = old.steps, old.messages
                target.observed, target.pruned = old.observed, old.pruned
                target.started = old.started or stamp
            while len(self.turns) > self.max_turns:
                self.turns.popitem(last=False)
        return self.turns[turn_id]

    def step(self, turn, step):
        old = turn.steps.get(step["id"])
        if old:
            old.update({k: v for k, v in step.items() if v is not None})
        else:
            turn.steps[step["id"]] = step
            turn.observed += 1
        while len(turn.steps) > self.max_steps:
            turn.steps.popitem(last=False)
            turn.pruned += 1
        self.changed += 1

    def consume(self, record):
        """Only allowlisted public events/fields enter the view model."""
        kind = record.get("type")
        p = record.get("payload")
        if not isinstance(p, dict):
            return
        stamp = timestamp(record.get("timestamp"))
        self.last_record = stamp or self.last_record
        event = str(p.get("type", ""))
        if kind == "turn_context":
            turn_id = str(p.get("turn_id") or self.current_id)
            self.turn(turn_id, stamp)
            self.current_id = turn_id
            self.changed += 1
            return
        if kind == "event_msg" and event in ("task_started", "task_start"):
            turn_id = str(p.get("turn_id") or self.current_id)
            t = self.turn(turn_id, stamp)
            self.current_id = turn_id
            t.status, t.started, t.ended = "active", timestamp(p.get("started_at")) or stamp, None
            self.changed += 1
            return
        if kind == "event_msg" and event in ("task_complete", "task_completed", "turn_aborted", "task_failed"):
            t = self.turn(p.get("turn_id"), stamp)
            t.status = "completed" if event in ("task_complete", "task_completed") else "interrupted" if event == "turn_aborted" else "failed"
            t.ended = timestamp(p.get("completed_at")) or stamp
            t.started = timestamp(p.get("started_at")) or t.started
            if p.get("last_agent_message"):
                t.summary = clean_text(p["last_agent_message"], 420)
            for step in t.steps.values():
                if step["status"] == "active":
                    step["status"] = "unknown"
            self.changed += 1
            return
        if kind == "event_msg" and event == "item_completed":
            item = p.get("item", {})
            if not isinstance(item, dict):
                return
            item_type = str(item.get("type", "")).lower().replace("_", "")
            t = self.turn(p.get("turn_id"), stamp)
            if p.get("turn_id") and self.current_id == "recent":
                self.current_id = str(p["turn_id"])
            if item_type == "agentmessage":
                phase = item.get("phase", "")
                text = clean_text(item.get("content", ""), 300)
                if text and phase in ("commentary", "final"):
                    msg_id = str(item.get("id", ""))
                    if not any(m["id"] == msg_id for m in t.messages):
                        t.messages.append({"id": msg_id, "time": stamp, "text": text, "phase": phase})
                        if phase == "final":
                            t.summary = text
                        self.changed += 1
                return
            allowed = {"commandexecution", "filechange", "websearch", "mcptoolcall", "dynamictoolcall", "collabagenttoolcall", "contextcompaction"}
            if item_type not in allowed:
                return
            tool = item.get("tool", item.get("name", item.get("type", "tool")))
            group = "plan" if item_type == "contextcompaction" else category(tool, item.get("command", ""))
            label = "整理上下文" if item_type == "contextcompaction" else CATEGORIES[group]
            self.step(t, {"id": str(item.get("id") or f"item:{stamp}"), "category": group, "label": label, "tool": clean_text(tool, 80), "source": "执行事件", "status": result_status(item), "start": timestamp(p.get("started_at_ms")) or stamp, "end": timestamp(p.get("completed_at_ms")) or stamp, "exitCode": item.get("exit_code"), "batch": False})
            return
        if kind != "response_item":
            return
        t = self.turn(stamp=stamp)
        if event in ("function_call", "custom_tool_call"):
            name = str(p.get("name", "tool"))
            group = category(name, p.get("arguments", p.get("input", "")))
            batch = name.lower() in ("exec", "functions.exec")
            self.step(t, {"id": str(p.get("call_id") or p.get("id") or f"call:{stamp}"), "category": "plan" if batch else group, "label": "工具批次" if batch else CATEGORIES[group], "tool": clean_text(name, 80), "source": "工具请求", "status": "active", "start": stamp, "end": None, "exitCode": None, "batch": batch})
            # A recorded request proves a turn was running at that timestamp.
            if t.status == "unknown":
                t.status = "active"
        elif event in ("function_call_output", "custom_tool_call_output"):
            call_id = str(p.get("call_id", ""))
            step = t.steps.get(call_id)
            if step:
                step["status"], step["end"] = "returned", stamp
                # Parse only status metadata, never retain tool output text.
                output = p.get("output")
                if isinstance(output, str) and len(output) < MAX_LINE:
                    try:
                        data = json.loads(output)
                        if isinstance(data, dict):
                            if data.get("isError") is True:
                                step["status"] = "failed"
                            elif "exit_code" in data:
                                step["status"] = "complete" if data["exit_code"] == 0 else "failed"
                                step["exitCode"] = data["exit_code"]
                    except ValueError:
                        pass
                self.changed += 1

    def poll(self):
        with self.lock:
            self.last_check = time.time()
            try:
                stat = self.path.stat()
                identity = (stat.st_dev, stat.st_ino)
                self.source_bytes = stat.st_size
                reset = not self.initialized or stat.st_size < self.offset or self.identity != identity
                if reset:
                    self.turns.clear()
                    self.current_id = "recent"
                    start = max(0, stat.st_size - self.tail_bytes)
                    self.offset = start
                    self.partial_history = start > 0
                    self.decoder = LineDecoder(self.consume, skip_first=start > 0)
                    self.identity = identity
                    self.initialized = True
                    self.changed += 1
                budget = self.tail_bytes if reset else INCREMENT_BUDGET
                with self.path.open("rb") as file:
                    file.seek(self.offset)
                    remaining = min(stat.st_size - self.offset, budget)
                    while remaining > 0:
                        data = file.read(min(remaining, READ_CHUNK))
                        if not data:
                            break
                        self.decoder.feed(data)
                        self.offset += len(data)
                        self.read_bytes += len(data)
                        remaining -= len(data)
                self.scanning = self.offset < stat.st_size
                self.error = None
            except (OSError, ValueError) as exc:
                self.error = "记录暂时不可读：" + type(exc).__name__

    def run(self):
        interval = max(.5, min(5, float(self.config.get("poll_seconds", 1))))
        while not self.closed.is_set():
            self.poll()
            self.closed.wait(interval)

    def snapshot(self):
        with self.lock:
            turns = []
            for turn in self.turns.values():
                steps = sorted(turn.steps.values(), key=lambda s: s["start"] or 0)
                counts = collections.Counter(s["category"] for s in steps)
                turns.append({"id": turn.id, "status": turn.status, "start": turn.started, "end": turn.ended, "steps": [dict(s) for s in steps], "messages": list(turn.messages), "summary": turn.summary, "observed": turn.observed, "pruned": turn.pruned, "categories": dict(counts)})
            return {"schema": 1, "revision": self.changed, "thread": {"id": self.thread_id, "title": self.config["title"]}, "connection": {"mode": "local-readonly", "ok": self.error is None, "error": self.error, "lastRecord": self.last_record, "lastCheck": self.last_check, "partialHistory": self.partial_history, "skippedLargeOrInvalidLines": self.decoder.skipped, "catchingUp": self.scanning, "sourceBytes": self.source_bytes, "readBytes": self.read_bytes, "retainedSteps": sum(len(t.steps) for t in self.turns.values()), "maxRetainedSteps": self.max_steps * self.max_turns}, "turns": turns, "now": time.time()}


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


# Reuse types and handles instead of rebuilding them on every UI sync.
_memory_api = None
if os.name == "nt":
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel.GetCurrentProcess.restype = ctypes.c_void_p
    _psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MemoryCounters), ctypes.c_ulong]
    _memory_api = (_kernel.GetCurrentProcess(), _psapi.GetProcessMemoryInfo)


def process_memory():
    """Measure this observer process only, without enumerating other apps."""
    if _memory_api is None:
        return {"workingSetMiB": None, "privateMiB": None, "scope": "observer-only"}
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle, get_info = _memory_api
    if not get_info(handle, ctypes.byref(counters), counters.cb):
        return {"workingSetMiB": None, "privateMiB": None, "scope": "observer-only"}
    return {"workingSetMiB": round(counters.WorkingSetSize / 1048576, 1), "privateMiB": round(counters.PrivateUsage / 1048576, 1), "scope": "observer-only"}
