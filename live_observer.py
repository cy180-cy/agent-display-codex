"""Bounded public-event enrichment for the local read-only Agent display.

The observer reads the one configured rollout only. Recorded code is display
data: this module never evaluates it or opens paths mentioned by a tool call.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from observer import LineDecoder, Observer, READ_CHUNK, timestamp  # noqa: E402


MAX_CODE_ENTRIES = 64
MAX_ENTRY_BYTES = 8 * 1024
MAX_CODE_CACHE_BYTES = 512 * 1024
MAX_METADATA_BACKFILL_BYTES = 32 * 1024 * 1024
USAGE_KEYS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens",
)
PUBLIC_FINAL_PHASES = {"final", "final_answer"}
PUBLIC_MESSAGE_PHASES = PUBLIC_FINAL_PHASES | {"commentary"}


def _clip(value, limit):
    encoded = str(value or "").encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return encoded.decode("utf-8"), False
    marker = "\n…[展示已截断]"
    return (encoded[:max(0, limit - len(marker.encode("utf-8")))]
            .decode("utf-8", errors="ignore") + marker), True


def _secret_key(value):
    key = re.sub(r"[^a-z0-9]", "", value.lower())
    return (
        any(part in key for part in ("apikey", "password", "passwd", "secret", "credential", "session", "cookie"))
        or key in {"token", "authorization", "pwd", "rawcontent", "summarytext", "encryptedcontent", "chainofthought"}
        or key.endswith(("accesstoken", "authtoken", "refreshtoken", "privatetoken", "bottoken"))
        or (key.endswith("token") and key not in {"maxtoken", "inputtoken", "outputtoken", "totaltoken"})
    )


def redact(value):
    """Remove credentials and explicit private reasoning blocks, not execute code."""
    text = str(value or "")
    original = text
    text = re.sub(r"<oai-mem-citation>[\s\S]*?</oai-mem-citation>", "", text)
    text = re.sub(r"(?is)<(?:think|analysis|reasoning)\b[^>]*>.*?</(?:think|analysis|reasoning)>", "[内部内容已省略]", text)
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[凭据已隐藏]", text)
    text = re.sub(r"(?i)\b(?:sk-|sess-)[a-z0-9_-]{10,}\b", "[凭据已隐藏]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16})\b", "[凭据已隐藏]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[凭据已隐藏]", text)
    text = re.sub(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[凭据已隐藏]", text)
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@", r"\1[凭据已隐藏]@", text)

    # Tool calls may themselves contain JSON strings with escaped quotes.
    escaped = re.compile(r"(?P<quote>\\[\"'])(?P<key>[A-Za-z0-9_.$:-]{1,128})(?P=quote)(?P<sep>\s*[:=]\s*)(?P=quote)[\s\S]*?(?P=quote)", re.I)
    text = escaped.sub(lambda m: (m["quote"] + m["key"] + m["quote"] + m["sep"] +
                                 m["quote"] + "[凭据已隐藏]" + m["quote"])
                       if _secret_key(m["key"]) else m.group(0), text)
    heredoc = re.compile(r"(?P<key>\$env:[A-Za-z0-9_]+)(?P<sep>\s*=\s*)@(?P<quote>[\"'])\r?\n[\s\S]*?\r?\n(?P=quote)@", re.I)
    text = heredoc.sub(lambda m: m["key"] + m["sep"] + '"[凭据已隐藏]"'
                       if _secret_key(m["key"]) else m.group(0), text)

    assignment = re.compile(
        r"(?<![A-Za-z0-9_.$:-])(?P<quote>[\"']?)(?P<key>(?:[A-Za-z0-9_.$:-]{0,96}(?:api[_-]?key|password|passwd|secret|credential|session|cookie)[A-Za-z0-9_.$:-]{0,96}|[A-Za-z0-9_.$:-]{0,96}token|authorization|pwd|raw_content|summary_text|encrypted_content|chain_of_thought))(?P=quote)"
        r"(?P<sep>\s*[:=]\s*)"
        r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]&]+)", re.I)

    def replace_assignment(match):
        if not _secret_key(match["key"]):
            return match.group(0)
        quote = match["quote"]
        return f"{quote}{match['key']}{quote}{match['sep']}\"[凭据已隐藏]\""

    text = assignment.sub(replace_assignment, text)
    flags = re.compile(r"(?P<flag>--[\w-]+)(?P<sep>\s+|=)(?P<value>\"[^\"]*\"|'[^']*'|[^\s]+)")
    text = flags.sub(lambda m: m["flag"] + m["sep"] + '"[凭据已隐藏]"'
                     if _secret_key(m["flag"]) else m.group(0), text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text, text != original


def _public_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_public_text(part) for part in value
                         if isinstance(part, dict) and str(part.get("type", "")).lower() in
                         {"text", "input_text", "output_text"})
    if isinstance(value, dict):
        return str(value.get("text", "")) if isinstance(value.get("text"), str) else ""
    return ""


def _answer_text(value, depth=0):
    """Only human-readable answer fields; never serialize internal reply IDs."""
    if depth > 2:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "、".join(text for part in value[:12] if (text := _answer_text(part, depth + 1)))
    if isinstance(value, dict):
        for key in ("answer", "text", "label", "value", "selectedOptions"):
            if key in value:
                return _answer_text(value[key], depth + 1)
    return ""


def _public_user_text(value):
    """Normalize known UI envelopes into the user's visible request text.

    This parser only returns text. Reply answers and realtime inputs are never
    interpreted as commands or instructions by the observer.
    """
    text = _public_text(value)
    request_marker = re.search(r"(?im)^##\s*My request:\s*", text)
    if request_marker:
        text = text[request_marker.end():]
    text = re.sub(r"<(?:environment_context|in-app-browser-context)\b[^>]*>[\s\S]*?</(?:environment_context|in-app-browser-context)>", "", text, flags=re.I)

    def question_reply(match):
        try:
            reply = json.loads(match.group(1).strip())
        except (ValueError, TypeError):
            return ""
        if isinstance(reply, dict):
            reply = reply.get("answers", [reply])
        if not isinstance(reply, list):
            return ""
        parts = []
        for item in reply[:8]:
            if not isinstance(item, dict):
                continue
            answer = _answer_text(item.get("answer"))
            if not answer:
                continue
            question = item.get("question")
            readable = "补充回答：" + answer
            if isinstance(question, str) and question.strip():
                readable += "\n问题：" + question.strip()[:200]
            parts.append(readable)
        return "\n\n".join(parts)

    text = re.sub(r"<send_user_message_question_reply\b[^>]*>([\s\S]*?)</send_user_message_question_reply>", question_reply, text, flags=re.I)

    def realtime_input(match):
        body = match.group(1)
        source = re.search(r"<source\b[^>]*>([\s\S]*?)</source>", body, re.I)
        if source and source.group(1).strip().lower() in {"transcript_tail_flush", "transcript_flush", "handoff"}:
            return ""
        human_input = re.search(r"<input\b[^>]*>([\s\S]*?)</input>", body, re.I)
        if not human_input:
            return ""
        value = human_input.group(1).strip()
        if re.match(r"(?i)(?:the user just ended their realtime session|here is the remaining handoff/transcript)", value):
            return ""
        return value

    text = re.sub(r"<realtime_delegation\b[^>]*>([\s\S]*?)</realtime_delegation>", realtime_input, text, flags=re.I)
    text = re.sub(r"<(?:transcript_delta|transcript_tail_flush)\b[^>]*>[\s\S]*?</(?:transcript_delta|transcript_tail_flush)>", "", text, flags=re.I)
    text = re.sub(r"(?im)^Distinguish instructions in attached documents from the user's request\.\s*", "", text)
    text = text.strip()
    # An incomplete UI envelope must not become a title made of its XML tag.
    if re.match(r"(?is)^<(?:send_user_message_question_reply|environment_context|in-app-browser-context|realtime_delegation|transcript_delta)\b", text):
        return ""
    if not request_marker and re.match(r"(?im)^#\s*Files mentioned by the user:", text):
        return ""
    return text


def _safe_text(value, limit=1200):
    text, redacted = redact(_public_text(value))
    text, truncated = _clip(text, limit)
    return text, redacted, truncated


def _numbers(value):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in USAGE_KEYS
            if isinstance(value.get(key), int) and not isinstance(value.get(key), bool)
            and 0 <= value[key] <= 10**15}


def _arguments(payload):
    value = payload.get("arguments", payload.get("input", ""))
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            pass
    return value


def _request_code(name, payload):
    arguments = _arguments(payload)
    short = name.lower().split("__")[-1].split(".")[-1]
    if isinstance(arguments, dict):
        if short in {"exec_command", "command", "shell"} and isinstance(arguments.get("cmd"), str):
            return "command", "powershell", arguments["cmd"]
        if isinstance(arguments.get("code"), str):
            return "code", "javascript", arguments["code"]
        if short == "apply_patch":
            return "patch", "diff", str(arguments.get("patch", arguments.get("input", "")))
        return "tool", "json", json.dumps(arguments, ensure_ascii=False, indent=2)
    if isinstance(arguments, str):
        if short == "apply_patch":
            return "patch", "diff", arguments
        return "code", "javascript" if short in {"exec", "js"} else "text", arguments
    return "tool", "text", ""


def _command_text(command):
    if isinstance(command, str):
        return command
    if not isinstance(command, list):
        return ""
    command = [str(part) for part in command]
    for index, part in enumerate(command[:-1]):
        if part.lower() in {"-command", "-c", "/c"}:
            return " ".join(command[index + 1:])
    return subprocess.list2cmdline(command)


def _output_status(value, depth=0):
    if depth > 3:
        return None, None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None, None
    if isinstance(value, dict):
        if value.get("isError") is True:
            return "failed", None
        exit_code = value.get("exit_code", value.get("exitCode"))
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            return ("complete" if exit_code == 0 else "failed"), exit_code
        for key in ("text", "content", "output"):
            if key in value:
                status, code = _output_status(value[key], depth + 1)
                if status:
                    return status, code
    if isinstance(value, list):
        statuses = [_output_status(part, depth + 1) for part in value if isinstance(part, (str, dict))]
        return next((pair for pair in statuses if pair[0] == "failed"),
                    next((pair for pair in statuses if pair[0]), (None, None)))
    return None, None


class LiveObserver(Observer):
    def __init__(self, config):
        super().__init__(config)
        self.code_entries = collections.OrderedDict()
        self.tasks = collections.OrderedDict()
        self.plans = collections.OrderedDict()
        self.plan_requests = collections.OrderedDict()
        self.usages = collections.OrderedDict()
        self.session_usage = None
        self.models = collections.OrderedDict()
        self.seen_usage_responses = collections.OrderedDict()
        self.metadata_backfill_attempts = collections.OrderedDict()
        self.metadata_backfill = None
        self.metadata_tail_bytes = min(MAX_METADATA_BACKFILL_BYTES,
                                       max(1024, int(config.get("metadata_tail_bytes", MAX_METADATA_BACKFILL_BYTES))))

    def _prune_aux(self):
        retained = set(self.turns)
        for cache in (self.tasks, self.usages, self.models, self.plans, self.metadata_backfill_attempts):
            for turn_id in list(cache):
                if turn_id not in retained:
                    del cache[turn_id]
        for entry_id, entry in list(self.code_entries.items()):
            if entry["turnId"] not in retained:
                del self.code_entries[entry_id]
                continue
            turn = self.turns[entry["turnId"]]
            step = turn.steps.get(entry_id)
            if step and step["status"] != "active":
                entry["status"] = step["status"]
            elif turn.status in {"completed", "failed", "interrupted"} and entry["status"] == "active":
                entry["status"] = "unknown"
        while len(self.code_entries) > MAX_CODE_ENTRIES:
            self.code_entries.popitem(last=False)
        while (self.code_entries and len(json.dumps(list(self.code_entries.values()),
                ensure_ascii=False).encode("utf-8")) > MAX_CODE_CACHE_BYTES):
            self.code_entries.popitem(last=False)
        while len(self.seen_usage_responses) > 256:
            self.seen_usage_responses.popitem(last=False)

    def _entry(self, entry_id, turn_id, stamp, **values):
        entry_id = str(entry_id or f"record:{stamp}")[:180]
        prior = self.code_entries.get(entry_id, {})
        entry = {
            "id": entry_id, "turnId": str(turn_id)[:180], "time": prior.get("time", stamp),
            "updatedAt": stamp, "tool": prior.get("tool", ""), "kind": prior.get("kind", "tool"),
            "title": prior.get("title", "执行记录"), "language": prior.get("language", "text"),
            "code": prior.get("code", ""), "output": prior.get("output", ""),
            "status": prior.get("status", "unknown"), "source": prior.get("source", "公开执行记录"),
            "redacted": prior.get("redacted", False), "truncated": prior.get("truncated", False),
        }
        for key, value in values.items():
            if key in {"code", "output", "title", "tool", "source"}:
                safe, redacted, truncated = _safe_text(str(value), 6000 if key == "code" else 1800 if key == "output" else 160)
                entry[key] = safe
                entry["redacted"] = entry["redacted"] or redacted
                entry["truncated"] = entry["truncated"] or truncated
            elif key in entry:
                entry[key] = value
        # Bound the serialized entry, including metadata and JSON escaping.
        while len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) > MAX_ENTRY_BYTES:
            key = "output" if len(entry["output"]) > len(entry["code"]) else "code"
            entry[key] = entry[key][:max(0, len(entry[key]) // 2)]
            entry["truncated"] = True
        self.code_entries[entry_id] = entry
        self.code_entries.move_to_end(entry_id)
        self.changed += 1
        self._prune_aux()
        turn = self.turns.get(str(turn_id))
        if turn and entry_id in turn.steps:
            turn.steps[entry_id]["codeEntryId"] = entry_id
            turn.steps[entry_id]["displayLabel"] = entry["title"][:100]
        return entry

    def _record_plan(self, turn_id, payload, stamp):
        if not isinstance(payload, dict) or not isinstance(payload.get("plan"), list):
            return
        items, seen = [], collections.Counter()
        for value in payload["plan"][:80]:
            if not isinstance(value, dict):
                continue
            title = _safe_text(value.get("step", value.get("title", "")), 360)[0]
            status = value.get("status")
            if not title or status not in {"pending", "in_progress", "completed"}:
                continue
            # Stable across status updates and reordering; duplicate titles stay distinct.
            digest = hashlib.sha256(title.encode()).hexdigest()[:16]
            seen[digest] += 1
            items.append({"id": digest + ":" + str(seen[digest]), "title": title,
                          "status": status})
        if items:
            self.plans[turn_id] = {"items": items, "at": stamp, "source": "recorded-plan"}
            self.changed += 1

    def _message(self, turn, item, stamp, user=False):
        text = _public_user_text(item.get("content", "")) if user else _public_text(item.get("content", ""))
        text, _, _ = _safe_text(text, 1600 if user else 900)
        if not text:
            return
        if user:
            self.tasks[turn.id] = {"text": text, "title": text.splitlines()[0][:160], "at": stamp}
            self.tasks.move_to_end(turn.id)
        else:
            phase = str(item.get("phase", ""))
            if phase not in PUBLIC_MESSAGE_PHASES:
                return
            message_id = str(item.get("id") or f"message:{stamp}")
            normalized_phase = "final" if phase in PUBLIC_FINAL_PHASES else "commentary"
            existing = next((message for message in turn.messages if message["id"] == message_id), None)
            if existing is not None:
                existing.update({"time": stamp, "text": text, "phase": normalized_phase})
            else:
                turn.messages.append({"id": message_id, "time": stamp, "text": text, "phase": normalized_phase})
            if normalized_phase == "final":
                turn.summary = text
                turn.status, turn.ended = "completed", stamp
                for step in turn.steps.values():
                    if step["status"] == "active":
                        step["status"] = "unknown"
        self.changed += 1

    def _usage(self, payload, stamp, kind):
        if kind == "token_usage_record":
            if payload.get("thread_id") and payload["thread_id"] != self.thread_id:
                return
            response_id = str(payload.get("response_id", ""))[:180]
            if response_id and response_id in self.seen_usage_responses:
                return
            turn_id = str(payload.get("turn_id") or self.current_id)
            total, last = _numbers(payload.get("turn_token_usage")), _numbers(payload.get("usage"))
            if not total:
                return
            self.turn(turn_id, stamp)
            self.usages[turn_id] = {"available": True, "scope": "turn", "source": "token_usage_record",
                                   "total": total, "last": last, "at": stamp, "turnId": turn_id,
                                   "billingAvailable": False}
            self.usages.move_to_end(turn_id)
            if response_id:
                self.seen_usage_responses[response_id] = True
        else:
            info = payload.get("info", {})
            if not isinstance(info, dict):
                return
            total, last = _numbers(info.get("total_token_usage")), _numbers(info.get("last_token_usage"))
            if not total and not last:
                return
            self.session_usage = {"available": True, "scope": "session", "source": "event_msg.token_count",
                                  "total": total, "last": last, "at": stamp, "turnId": self.current_id,
                                  "billingAvailable": False}
        self.changed += 1
        self._prune_aux()

    def consume(self, record):
        with self.lock:
            payload = record.get("payload")
            if not isinstance(payload, dict):
                return
            # The selected rollout is fixed; reject explicit cross-thread records.
            if payload.get("thread_id") and payload["thread_id"] != self.thread_id:
                return
            if any(len(str(payload.get(key, ""))) > 180 for key in ("turn_id", "root_turn_id")):
                return
            kind, event = record.get("type"), str(payload.get("type", ""))
            stamp = timestamp(record.get("timestamp"))
            super().consume(record)
            for retained_turn in self.turns.values():
                retained_turn.summary = _safe_text(retained_turn.summary, 900)[0]
            if kind == "turn_context":
                model = payload.get("model")
                if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", model):
                    self.models[self.current_id] = model
                self._prune_aux()
                return
            if kind == "token_usage_record" or kind == "event_msg" and event == "token_count":
                self._usage(payload, stamp, kind)
                return
            if kind == "response_item" and event in {"reasoning", "analysis"}:
                return
            turn = self.turn(payload.get("turn_id") or self.current_id, stamp)
            if kind == "response_item" and event in {"function_call", "custom_tool_call"}:
                name = str(payload.get("name", "tool"))
                args = _arguments(payload)
                call_id = str(payload.get("call_id") or payload.get("id") or "")
                if name.split("__")[-1].split(".")[-1] == "update_plan" and isinstance(args, dict):
                    bounded = {"plan": [{"step": _safe_text(x.get("step", ""), 360)[0], "status": x.get("status")}
                                        for x in args.get("plan", [])[:80] if isinstance(x, dict)]} if isinstance(args.get("plan"), list) else {}
                    self.plan_requests[call_id] = (turn.id, bounded, stamp)
                    while len(self.plan_requests) > 32:
                        self.plan_requests.popitem(last=False)
                code_kind, language, code = _request_code(name, payload)
                if code:
                    title = "文件修改" if code_kind == "patch" else "执行命令" if code_kind == "command" else name.split("__")[-1]
                    if isinstance(args, dict):
                        title = next((args[k] for k in ("justification", "description", "title")
                                      if isinstance(args.get(k), str) and args[k].strip()), title)
                    self._entry(payload.get("call_id") or payload.get("id"), turn.id, stamp,
                                tool=name, kind=code_kind, language=language, code=code,
                                title=title, status="active", source="公开工具请求")
            elif kind == "response_item" and event in {"function_call_output", "custom_tool_call_output"}:
                output = _public_text(payload.get("output"))
                status, exit_code = _output_status(payload.get("output"))
                entry_id = str(payload.get("call_id") or payload.get("id") or "")
                plan_request = self.plan_requests.pop(entry_id, None)
                if plan_request and status != "failed":
                    self._record_plan(plan_request[0], plan_request[1], stamp)
                if output:
                    self._entry(entry_id, turn.id, stamp, output=output, status=status or "returned")
                if status and entry_id in turn.steps:
                    turn.steps[entry_id]["status"] = status
                    if exit_code is not None:
                        turn.steps[entry_id]["exitCode"] = exit_code
            elif kind == "response_item" and event == "message":
                role = payload.get("role")
                if role == "user":
                    self._message(turn, payload, stamp, user=True)
                elif role == "assistant" and payload.get("phase") in PUBLIC_MESSAGE_PHASES:
                    self._message(turn, payload, stamp)
            elif kind == "event_msg" and event == "item_completed":
                item = payload.get("item", {})
                if not isinstance(item, dict):
                    return
                item_type = str(item.get("type", "")).lower().replace("_", "")
                if item_type == "usermessage":
                    self._message(turn, item, stamp, user=True)
                elif item_type == "agentmessage":
                    self._message(turn, item, stamp)
                elif item_type == "commandexecution":
                    code = _command_text(item.get("command"))
                    if code:
                        status = turn.steps.get(str(item.get("id")), {}).get("status", "returned")
                        self._entry(item.get("id"), turn.id, stamp, kind="command", language="powershell",
                                    tool="CommandExecution", title="执行 " + code.strip().splitlines()[0][:100], code=code,
                                    output=_public_text(item.get("aggregated_output")), status=status,
                                    source="公开命令执行事件")
                elif item_type == "filechange" and isinstance(item.get("changes"), dict):
                    fragments, names = [], []
                    for file_path, change in list(item["changes"].items())[:8]:
                        if not isinstance(change, dict):
                            continue
                        name = str(file_path).replace("\\", "/").rsplit("/", 1)[-1]
                        names.append(name)
                        body = change.get("unified_diff", change.get("content", ""))
                        if isinstance(body, str):
                            fragments.append(f"--- {name}\n{body[:12000]}")
                    if fragments:
                        status = turn.steps.get(str(item.get("id")), {}).get("status", "returned")
                        self._entry(item.get("id"), turn.id, stamp, kind="patch", language="diff",
                                    tool="FileChange", title="修改 " + ", ".join(names), code="\n\n".join(fragments),
                                    status=status, source="公开文件修改事件")
            self._prune_aux()

    def poll(self):
        with self.lock:
            try:
                stat = self.path.stat()
                identity = (stat.st_dev, stat.st_ino)
                if self.initialized and (stat.st_size < self.offset or identity != self.identity):
                    self.code_entries.clear()
                    self.plans.clear()
                    self.plan_requests.clear()
                    self.tasks.clear()
                    self.usages.clear()
                    self.models.clear()
                    self.session_usage = None
                    self.seen_usage_responses.clear()
                    self.metadata_backfill_attempts.clear()
                    self.metadata_backfill = None
            except OSError:
                pass
            super().poll()
            self._prune_aux()
            latest = next(reversed(self.turns.values()), None)
            if (latest and not self.error and self.initialized
                    and latest.id not in self.metadata_backfill_attempts
                    and (not self.tasks.get(latest.id, {}).get("text") or not self.models.get(latest.id))):
                self._backfill_public_metadata(latest.id)

    def _backfill_public_metadata(self, target_turn_id):
        """Recover task/model metadata only from a bounded part of this rollout.

        A user message must explicitly name the target turn or follow a turn
        context encountered inside the scan. A mid-turn tail never inherits
        the live observer's current turn as an assumed metadata association.
        """
        self.metadata_backfill_attempts[target_turn_id] = True
        self.metadata_backfill_attempts.move_to_end(target_turn_id)
        stats = {"targetTurnId": target_turn_id, "bytesRead": 0,
                 "maxBytes": self.metadata_tail_bytes, "taskFound": False,
                 "modelFound": False, "source": "selected-rollout-public-metadata"}
        context_turn_id = None
        explicit_task, contextual_task, candidate_model = None, None, None

        def metadata_record(record):
            nonlocal context_turn_id, explicit_task, contextual_task, candidate_model
            payload = record.get("payload")
            if not isinstance(payload, dict):
                return
            if payload.get("thread_id") and payload["thread_id"] != self.thread_id:
                return
            kind = record.get("type")
            stamp = timestamp(record.get("timestamp"))
            if kind == "turn_context":
                context_turn_id = str(payload.get("turn_id") or "") or None
                model = payload.get("model")
                if (context_turn_id == target_turn_id and isinstance(model, str)
                        and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", model)):
                    candidate_model = model
                return
            if kind == "event_msg" and payload.get("type") == "item_completed":
                item = payload.get("item")
                if (isinstance(item, dict)
                        and str(item.get("type", "")).lower().replace("_", "") == "usermessage"):
                    if not _public_user_text(item.get("content", "")):
                        return
                    explicit_turn = payload.get("turn_id")
                    if explicit_turn == target_turn_id:
                        explicit_task = (item, stamp)
                    elif not explicit_turn and context_turn_id == target_turn_id:
                        contextual_task = (item, stamp)
                return
            if (kind == "response_item" and payload.get("type") == "message"
                    and payload.get("role") == "user"):
                if not _public_user_text(payload.get("content", "")):
                    return
                explicit_turn = payload.get("turn_id")
                if explicit_turn == target_turn_id:
                    explicit_task = (payload, stamp)
                elif not explicit_turn and context_turn_id == target_turn_id:
                    contextual_task = (payload, stamp)

        try:
            with self.path.open("rb") as source:
                stat = os.fstat(source.fileno())
                if (stat.st_dev, stat.st_ino) != self.identity:
                    stats["error"] = "source_changed"
                    self.metadata_backfill = stats
                    return
                start = max(0, stat.st_size - self.metadata_tail_bytes)
                source.seek(start)
                decoder = LineDecoder(metadata_record, skip_first=start > 0)
                remaining = min(stat.st_size, self.metadata_tail_bytes)
                while remaining:
                    chunk = source.read(min(READ_CHUNK, remaining))
                    if not chunk:
                        break
                    decoder.feed(chunk)
                    stats["bytesRead"] += len(chunk)
                    remaining -= len(chunk)
                stats["skippedLines"] = decoder.skipped
        except OSError as exc:
            stats["error"] = type(exc).__name__
        else:
            turn = self.turns.get(target_turn_id)
            candidate = explicit_task or contextual_task
            if turn and candidate and not self.tasks.get(target_turn_id, {}).get("text"):
                self._message(turn, candidate[0], candidate[1], user=True)
                if self.tasks.get(target_turn_id, {}).get("text"):
                    self.tasks[target_turn_id]["source"] = "metadata-backfill"
                    stats["taskFound"] = True
            if candidate_model and not self.models.get(target_turn_id):
                self.models[target_turn_id] = candidate_model
                stats["modelFound"] = True
                self.changed += 1
        self.metadata_backfill = stats
        self._prune_aux()

    def clear_history(self):
        with self.lock:
            result = super().clear_history()
            self._prune_aux()
            latest = next(reversed(self.turns.values()), None)
            entries = [entry for entry in self.code_entries.values() if latest and entry["turnId"] == latest.id]
            keep = {entry["id"] for entry in entries[-16:]}
            keep.update(entry["id"] for entry in entries if entry["status"] == "active")
            before = len(self.code_entries)
            self.code_entries = collections.OrderedDict((key, entry) for key, entry in self.code_entries.items() if key in keep)
            self._prune_aux()
            result["removedCodeEntries"] = before - len(self.code_entries)
            result["retainedCodeEntries"] = len(self.code_entries)
            self.last_cleanup.update(result)
            return result

    @staticmethod
    def _usage_view(value):
        if not value:
            return {"available": False, "scope": None, "source": None, "total": {}, "last": {},
                    "cacheHitRate": None, "billingAvailable": False}
        view = {**value, "total": dict(value["total"]), "last": dict(value["last"])}
        total = view["total"]
        inputs, cached = total.get("input_tokens"), total.get("cached_input_tokens")
        view["cacheHitRate"] = (cached / inputs if inputs and cached is not None and cached <= inputs else None)
        return view

    def snapshot(self):
        with self.lock:
            result = super().snapshot()
            self._prune_aux()
            for turn in result["turns"]:
                task = self.tasks.get(turn["id"], {})
                turn["task"], turn["taskTitle"] = task.get("text", ""), task.get("title", "")
                turn["taskSource"] = task.get("source", "public-live-event") if task.get("text") else None
                turn["usage"] = self._usage_view(self.usages.get(turn["id"]))
                turn["model"] = self.models.get(turn["id"])
                turn["plan"] = self.plans.get(turn["id"])
            latest = result["turns"][-1] if result["turns"] else None
            turn_id = latest["id"] if latest else self.current_id
            result["usage"] = self._usage_view(self.usages.get(turn_id) or self.session_usage)
            result["codeEntries"] = sorted((dict(entry) for entry in self.code_entries.values()),
                                            key=lambda entry: (entry.get("time") or 0, entry["id"]))
            task = self.tasks.get(turn_id, {})
            result["task"], result["taskTitle"] = task.get("text", ""), task.get("title", "")
            result["taskSource"] = task.get("source", "public-live-event") if task.get("text") else None
            result["model"] = self.models.get(turn_id)
            result["connection"]["metadataBackfill"] = dict(self.metadata_backfill) if self.metadata_backfill else None
            result["dataAvailability"] = {
                "tokens": result["usage"]["available"], "code": bool(result["codeEntries"]),
                "cost": False, "qualityScore": False, "confidence": False,
                "progress": False, "agentTopology": False, "plan": bool(latest and latest.get("plan")),
            }
            result["codeCache"] = {"retainedEntries": len(self.code_entries), "maxEntries": MAX_CODE_ENTRIES,
                                   "bytes": len(json.dumps(result["codeEntries"], ensure_ascii=False).encode("utf-8")),
                                   "maxBytes": MAX_CODE_CACHE_BYTES}
            return result
