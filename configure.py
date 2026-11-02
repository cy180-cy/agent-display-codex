"""Configure one explicitly selected Codex rollout; never scan chat history."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
ID_PATTERN = re.compile(r"[A-Za-z0-9-]{1,128}")


def make_config(rollout: Path, title: str, thread_id: str | None = None):
    rollout = rollout.expanduser().resolve()
    if not rollout.is_file() or rollout.suffix.lower() != ".jsonl":
        raise ValueError("请选择一个存在的 .jsonl 执行记录文件")
    # A bounded read of the selected file only; no other chat is opened.
    metadata_id = None
    with rollout.open("rb") as stream:
        for line in stream.read(64 * 1024).splitlines():
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(record, dict) and record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
                metadata_id = record["payload"].get("id")
                break
    filename_id = re.search(r"([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$", rollout.stem)
    detected = metadata_id or (filename_id.group(1) if filename_id else None)
    if thread_id and detected and thread_id != detected:
        raise ValueError("指定的对话 ID 与所选记录不一致")
    selected = thread_id or detected
    if not isinstance(selected, str) or not ID_PATTERN.fullmatch(selected):
        raise ValueError("无法识别对话 ID，请使用 --thread-id 明确指定")
    title = title.strip()
    if not title or len(title) > 80:
        raise ValueError("显示名称需要 1 到 80 个字符")
    return {"thread_id": selected, "title": title, "rollout_path": str(rollout),
            "poll_seconds": 1, "tail_bytes": 8 * 1024 * 1024,
            "max_steps_per_turn": 160, "max_turns": 4}


def main():
    parser = argparse.ArgumentParser(description="选择一个 Codex 对话，生成仅保存在本机的配置")
    parser.add_argument("--rollout", type=Path, help="所选对话的 JSONL 记录路径")
    parser.add_argument("--title", help="看板显示名称")
    parser.add_argument("--thread-id", help="通常自动识别，无需填写")
    parser.add_argument("--output", type=Path, default=BASE / "config.local.json")
    parser.add_argument("--force", action="store_true", help="覆盖已有本地配置")
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        parser.error("配置已经存在。需要更换对话时请添加 --force")
    try:
        rollout = args.rollout or Path(input("执行记录完整路径：").strip().strip('"'))
        title = args.title or input("看板显示名称：").strip()
        config = make_config(rollout, title, args.thread_id)
        args.output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", "utf-8")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print("本地配置已保存。运行 python build_live.py，然后运行 python server.py --open")


if __name__ == "__main__":
    main()
