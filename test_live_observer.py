import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from live_observer import LiveObserver, MAX_CODE_CACHE_BYTES, MAX_ENTRY_BYTES, MAX_METADATA_BACKFILL_BYTES, _public_user_text, redact


THREAD = "fixture-knowledge-thread"


def record(kind, payload, second=0):
    return {"type": kind, "timestamp": 1793232000 + second, "payload": payload}


class LiveObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".observer-test-", dir=Path(__file__).resolve().parent)
        self.path = Path(self.temp.name) / ("rollout-fixture-" + THREAD + ".jsonl")
        self.path.write_text("", encoding="utf-8")
        self.config = {"thread_id": THREAD, "title": "示例任务", "rollout_path": str(self.path),
                       "tail_bytes": 4 * 1024 * 1024, "max_steps_per_turn": 160, "max_turns": 4}
        self.observer = LiveObserver(self.config)
        self.observer.consume(record("turn_context", {"turn_id": "turn-a", "model": "fixture-model"}))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, call_id, code="text('hello');", second=1):
        self.observer.consume(record("response_item", {"type": "custom_tool_call", "name": "exec",
                                                      "call_id": call_id, "input": code}, second))

    def test_code_request_and_public_command_are_available(self):
        self.call("call-1", "await tools.exec_command({cmd: 'Get-Date'});")
        self.observer.consume(record("event_msg", {"type": "item_completed", "turn_id": "turn-a",
            "item": {"type": "CommandExecution", "id": "command-1", "command": ["pwsh.exe", "-Command", "Get-Date"],
                     "status": "completed", "exit_code": 0, "aggregated_output": "2026-10-29"}}, 2))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["codeEntries"][0]["language"], "javascript")
        command = next(entry for entry in snapshot["codeEntries"] if entry["id"] == "command-1")
        self.assertEqual(command["code"], "Get-Date")
        self.assertEqual(command["status"], "complete")
        self.assertEqual(snapshot["turns"][0]["steps"][0]["codeEntryId"], "call-1")

    def test_credentials_in_code_and_output_are_redacted(self):
        secrets = ["fake-api-value", "fake-pass-value", "fake-session-value", "sk-fixtureABCDEFGHIJKLMN", "fake-bearer-value"]
        code = '$env:OPENAI_API_KEY="fake-api-value"; const cfg={"password":"fake-pass-value", session_id: "fake-session-value"}; key="sk-fixtureABCDEFGHIJKLMN";'
        self.call("call-secret", code)
        self.observer.consume(record("response_item", {"type": "custom_tool_call_output", "call_id": "call-secret",
            "output": [{"type": "text", "text": "Authorization: Bearer fake-bearer-value"}]}, 3))
        serialized = json.dumps(self.observer.snapshot(), ensure_ascii=False)
        for secret in secrets:
            self.assertNotIn(secret, serialized)
        self.assertTrue(self.observer.snapshot()["codeEntries"][0]["redacted"])
        safe, _ = redact("--api-key token-value --password=other-value input_tokens=123 total_tokens=456")
        self.assertNotIn("token-value", safe)
        self.assertNotIn("other-value", safe)
        self.assertIn("input_tokens=123", safe)

    def test_file_diff_only_uses_recorded_content(self):
        source = "NO_SOURCE_FILE_IS_OPENED"
        self.observer.consume(record("event_msg", {"type": "item_completed", "turn_id": "turn-a", "item": {
            "type": "FileChange", "id": "change-1", "status": "completed", "changes": {
                "Z:/unavailable/project/sample.py": {"type": "update", "unified_diff": "+" + source}}}}, 4))
        entry = self.observer.snapshot()["codeEntries"][0]
        self.assertIn(source, entry["code"])
        self.assertEqual(entry["title"], "修改 sample.py")

    def test_nested_json_and_env_heredoc_credentials_are_redacted(self):
        nested = r'const body = "{\"apiKey\":\"nested-secret value\"}";'
        safe, _ = redact(nested)
        self.assertNotIn("nested-secret", safe)
        safe, _ = redact("$env:CLIENT_SECRET = @'\nheredoc-secret\n'@")
        self.assertNotIn("heredoc-secret", safe)

    def test_parent_message_is_replaced_with_stronger_sanitized_text(self):
        self.observer.consume(record("event_msg", {"type": "item_completed", "turn_id": "turn-a", "item": {
            "type": "AgentMessage", "id": "message-secret", "phase": "commentary", "content": [{"type": "Text", "text": '{"apiKey":"message-secret-value"}'}]}}, 3))
        self.assertNotIn("message-secret-value", json.dumps(self.observer.snapshot(), ensure_ascii=False))

    def test_large_attachment_preamble_does_not_hide_user_request(self):
        text = "# Files mentioned by the user:\n" + "attachment metadata\n" * 300 + "\n## My request:\n核对真实执行结果"
        self.observer.consume(record("event_msg", {"type": "item_completed", "turn_id": "turn-a", "item": {
            "type": "UserMessage", "id": "user-large", "content": [{"type": "Text", "text": text}]}}, 1))
        self.assertEqual(self.observer.snapshot()["taskTitle"], "核对真实执行结果")

    def test_private_reasoning_is_never_retained(self):
        private = "PRIVATE_REASONING_SENTINEL"
        self.observer.consume(record("response_item", {"type": "reasoning", "summary": private, "encrypted_content": private}, 1))
        self.observer.consume(record("event_msg", {"type": "item_completed", "item": {"type": "Reasoning", "raw_content": private}}, 2))
        self.observer.consume(record("response_item", {"type": "message", "role": "assistant", "phase": "analysis", "content": [{"type": "text", "text": private}]}, 3))
        self.assertNotIn(private, json.dumps(self.observer.snapshot()))

    def test_user_task_and_final_answer_phase(self):
        self.observer.consume(record("event_msg", {"type": "item_completed", "turn_id": "turn-a", "item": {
            "type": "UserMessage", "id": "user-1", "content": [{"type": "Text", "text": "检查入库结果"}]}}, 1))
        self.observer.consume(record("event_msg", {"type": "item_completed", "turn_id": "turn-a", "item": {
            "type": "AgentMessage", "id": "answer-1", "phase": "final_answer", "content": [{"type": "Text", "text": "检查已完成"}]}}, 2))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["taskTitle"], "检查入库结果")
        self.assertEqual(snapshot["turns"][-1]["summary"], "检查已完成")
        self.assertEqual(snapshot["turns"][-1]["status"], "completed")

    def test_usage_sources_are_not_added_together(self):
        total = {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 100, "total_tokens": 1100}
        self.observer.consume(record("event_msg", {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 900000, "total_tokens": 990000}, "last_token_usage": total}}, 2))
        usage = {"thread_id": THREAD, "turn_id": "turn-a", "response_id": "response-1", "turn_token_usage": total, "usage": total}
        self.observer.consume(record("token_usage_record", usage, 3))
        self.observer.consume(record("token_usage_record", usage, 4))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["usage"]["total"]["total_tokens"], 1100)
        self.assertEqual(snapshot["usage"]["scope"], "turn")
        self.assertEqual(snapshot["usage"]["cacheHitRate"], .8)
        self.assertFalse(snapshot["usage"]["billingAvailable"])
        self.assertFalse(snapshot["dataAvailability"]["cost"])

    def test_session_usage_fallback_is_explicit(self):
        self.observer.consume(record("event_msg", {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": 2000}, "last_token_usage": {"total_tokens": 100}}}, 2))
        self.assertEqual(self.observer.snapshot()["usage"]["scope"], "session")
        self.assertFalse(self.observer.snapshot()["turns"][-1]["usage"]["available"])

    def test_code_cache_and_each_entry_are_bounded(self):
        for index in range(80):
            self.call("large-" + str(index), "字" * 12000, index + 1)
            self.observer.consume(record("response_item", {"type": "custom_tool_call_output", "call_id": "large-" + str(index),
                "output": [{"type": "text", "text": "输出" * 10000}]}, index + 2))
        entries = self.observer.snapshot()["codeEntries"]
        self.assertLessEqual(len(entries), 64)
        self.assertLessEqual(len(json.dumps(entries, ensure_ascii=False).encode()), MAX_CODE_CACHE_BYTES)
        for entry in entries:
            self.assertLessEqual(len(json.dumps(entry, ensure_ascii=False).encode()), MAX_ENTRY_BYTES)
            self.assertTrue(entry["truncated"])

    def test_clear_preserves_recent_code_and_pending_requests(self):
        for index in range(30):
            self.call("call-" + str(index), second=index + 1)
            if index != 0:
                self.observer.consume(record("response_item", {"type": "custom_tool_call_output", "call_id": "call-" + str(index), "output": "done"}, index + 2))
        cleanup = self.observer.clear_history()
        entries = self.observer.snapshot()["codeEntries"]
        self.assertEqual(len(entries), 17)
        self.assertIn("call-0", {entry["id"] for entry in entries})
        self.assertEqual(cleanup["scope"], "viewer-cache-only")
        self.assertEqual(cleanup["removedCodeEntries"], 13)

    def test_poll_clear_and_snapshot_do_not_write_source(self):
        lines = [record("turn_context", {"turn_id": "fixture-turn"}), record("response_item", {
            "type": "custom_tool_call", "name": "exec", "call_id": "source-call", "input": "text(1);"}, 1)]
        self.path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        fresh = LiveObserver(self.config)
        fresh.poll()
        self.assertEqual(len(fresh.snapshot()["codeEntries"]), 1)
        fresh.clear_history()
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_completed_turn_does_not_keep_stale_active_code(self):
        for index in range(25):
            self.call("pending-" + str(index), second=index + 1)
        self.observer.consume(record("event_msg", {"type": "task_completed", "turn_id": "turn-a"}, 30))
        self.observer.clear_history()
        entries = self.observer.snapshot()["codeEntries"]
        self.assertEqual(len(entries), 16)
        self.assertFalse(any(entry["status"] == "active" for entry in entries))

    def test_path_thread_mismatch_and_other_thread_records_are_rejected(self):
        with self.assertRaises(ValueError):
            LiveObserver({**self.config, "thread_id": "another-thread"})
        self.observer.consume(record("token_usage_record", {"thread_id": "another-thread", "turn_id": "turn-a",
            "response_id": "bad", "turn_token_usage": {"total_tokens": 123}}, 4))
        self.assertFalse(self.observer.snapshot()["usage"]["available"])

    def metadata_fixture(self, preceding, metadata_bytes=32 * 1024, current_user=None):
        # Force the ordinary tail reader to miss the earlier user request.
        filler = [record("world_state", {"state": "filler" * 80}, index + 20) for index in range(12)]
        ending = [record("turn_context", {"turn_id": "target-turn"}, 80)]
        if current_user:
            ending.append(record("event_msg", {"type": "item_completed", "turn_id": "target-turn", "item": {
                "type": "UserMessage", "id": "new-user", "content": [{"type": "Text", "text": current_user}]}}, 81))
        ending.append(record("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "tail-call", "input": "text('tail');"}, 82))
        self.path.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in preceding + filler + ending) + "\n", encoding="utf-8")
        config = {**self.config, "tail_bytes": 1024, "metadata_tail_bytes": metadata_bytes}
        return LiveObserver(config)

    def test_metadata_backfill_matches_turn_without_loading_historical_code(self):
        preceding = [
            record("turn_context", {"turn_id": "old-turn", "model": "old-model"}),
            record("event_msg", {"type": "item_completed", "turn_id": "old-turn", "item": {
                "type": "UserMessage", "id": "old-user", "content": [{"type": "Text", "text": "前一轮任务不能借用"}]}}, 1),
            record("turn_context", {"turn_id": "target-turn", "model": "actual-model"}, 2),
            record("event_msg", {"type": "item_completed", "turn_id": "target-turn", "item": {
                "type": "UserMessage", "id": "target-user", "content": [{"type": "Text", "text": "回填本轮示例任务请求"}]}}, 3),
            record("response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "historical-code", "input": "text('not-loaded');"}, 4),
        ]
        observer = self.metadata_fixture(preceding)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        observer.poll()
        snapshot = observer.snapshot()
        self.assertEqual(snapshot["taskTitle"], "回填本轮示例任务请求")
        self.assertEqual(snapshot["taskSource"], "metadata-backfill")
        self.assertEqual(snapshot["model"], "actual-model")
        self.assertEqual({entry["id"] for entry in snapshot["codeEntries"]}, {"tail-call"})
        self.assertTrue(snapshot["connection"]["metadataBackfill"]["taskFound"])
        self.assertLessEqual(snapshot["connection"]["metadataBackfill"]["bytesRead"], 32 * 1024)
        observer.poll()
        self.assertEqual(len(observer.metadata_backfill_attempts), 1)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_metadata_backfill_never_borrows_previous_turn_request(self):
        observer = self.metadata_fixture([
            record("turn_context", {"turn_id": "old-turn", "model": "old-model"}),
            record("event_msg", {"type": "item_completed", "turn_id": "old-turn", "item": {
                "type": "UserMessage", "id": "old-user", "content": [{"type": "Text", "text": "前一轮的任务"}]}}, 1),
        ])
        observer.poll()
        snapshot = observer.snapshot()
        self.assertEqual(snapshot["task"], "")
        self.assertIsNone(snapshot["model"])
        self.assertFalse(snapshot["connection"]["metadataBackfill"]["taskFound"])

    def test_metadata_backfill_requires_context_for_unscoped_user_messages(self):
        observer = self.metadata_fixture([
            record("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "没有轮次锚点，不可猜测"}]}),
            record("turn_context", {"turn_id": "target-turn", "model": "actual-model"}, 1),
        ])
        observer.poll()
        self.assertEqual(observer.snapshot()["task"], "")
        self.assertEqual(observer.snapshot()["model"], "actual-model")

    def test_metadata_backfill_respects_window_and_preserves_newer_task(self):
        preceding = [record("turn_context", {"turn_id": "target-turn"}),
            record("event_msg", {"type": "item_completed", "turn_id": "target-turn", "item": {
                "type": "UserMessage", "id": "user-old", "content": [{"type": "Text", "text": "窗口以外的旧请求"}]}}, 1)]
        observer = self.metadata_fixture(preceding, metadata_bytes=2048)
        observer.poll()
        snapshot = observer.snapshot()
        self.assertEqual(snapshot["task"], "")
        self.assertLessEqual(snapshot["connection"]["metadataBackfill"]["bytesRead"], 2048)
        observer = self.metadata_fixture(preceding, current_user="当前尾部请求必须保留")
        observer.poll()
        self.assertEqual(observer.snapshot()["taskTitle"], "当前尾部请求必须保留")
        self.assertNotEqual(observer.snapshot()["taskSource"], "metadata-backfill")

    def test_metadata_backfill_configuration_has_hard_32mb_cap(self):
        observer = LiveObserver({**self.config, "metadata_tail_bytes": 100 * 1024 * 1024})
        self.assertEqual(observer.metadata_tail_bytes, MAX_METADATA_BACKFILL_BYTES)

    def test_question_reply_envelope_exposes_only_readable_answer(self):
        payload = [{"questionItemId": '["request_user_input_async","call-internal-123",0]',
                    "question": "第一版优先连接哪个聊天？", "answer": "示例任务"}]
        wrapped = "<send_user_message_question_reply>\n" + json.dumps(payload, ensure_ascii=False) + "\n</send_user_message_question_reply>"
        self.observer.consume(record("event_msg", {"type": "item_completed", "turn_id": "turn-a", "item": {
            "type": "UserMessage", "id": "answer-user", "content": [{"type": "Text", "text": wrapped}]}}, 3))
        snapshot = self.observer.snapshot()
        self.assertEqual(snapshot["taskTitle"], "补充回答：示例任务")
        self.assertIn("第一版优先连接哪个聊天", snapshot["task"])
        self.assertNotIn("questionItemId", snapshot["task"])
        self.assertNotIn("call-internal", snapshot["task"])
        self.assertNotIn("send_user_message_question_reply", snapshot["task"])

    def test_context_and_realtime_flush_envelopes_are_not_tasks(self):
        controls = [
            "<environment_context>cwd=private-path</environment_context>",
            '<in-app-browser-context source="ambient-ui-state">URL=private-page</in-app-browser-context>',
            "<realtime_delegation><source>transcript_tail_flush</source><input>The user just ended their realtime session.</input><transcript_delta>assistant: hello</transcript_delta></realtime_delegation>",
            "<transcript_delta>assistant: old transcript</transcript_delta>",
            "<send_user_message_question_reply>not-valid-json-with-internal-id</send_user_message_question_reply>",
        ]
        for control in controls:
            self.assertEqual(_public_user_text(control), "")
        self.assertEqual(_public_user_text("<realtime_delegation><input>请检查入库状态</input><transcript_delta>assistant: old text</transcript_delta></realtime_delegation>"), "请检查入库状态")

    def test_my_request_body_survives_context_normalization(self):
        wrapped = '# Files mentioned by the user:\nattachment.png\n<in-app-browser-context source="ambient-ui-state">URL</in-app-browser-context>\n## My request:\n保留原版视觉效果'
        self.assertEqual(_public_user_text(wrapped), "保留原版视觉效果")
        self.assertEqual(_public_user_text("<environment_context>cwd</environment_context>\n继续检查实际任务"), "继续检查实际任务")

    def test_metadata_backfill_ignores_control_only_user_records(self):
        preceding = [
            record("turn_context", {"turn_id": "target-turn", "model": "actual-model"}),
            record("event_msg", {"type": "item_completed", "turn_id": "target-turn", "item": {
                "type": "UserMessage", "id": "human-user", "content": [{"type": "Text", "text": "真正的本轮任务"}]}}, 1),
            record("event_msg", {"type": "item_completed", "turn_id": "target-turn", "item": {
                "type": "UserMessage", "id": "control-user", "content": [{"type": "Text", "text": "<environment_context>context only</environment_context>"}]}}, 2),
        ]
        observer = self.metadata_fixture(preceding)
        observer.poll()
        self.assertEqual(observer.snapshot()["taskTitle"], "真正的本轮任务")


if __name__ == "__main__":
    unittest.main()
