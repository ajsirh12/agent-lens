#!/usr/bin/env python3
"""Emit fake Claude Code JSONL lines into a target file. Manual-testing tool."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def make_tool_use(sid: str, idx: int) -> dict:
    tuid = f"toolu_fake_{idx:06d}"
    return {
        "type": "assistant",
        "sessionId": sid,
        "uuid": str(uuid.uuid4()),
        "parentUuid": None,
        "isSidechain": False,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tuid,
                    "name": "Bash",
                    "input": {"command": f"echo hello {idx}"},
                }
            ],
        },
    }


def make_tool_result(sid: str, idx: int) -> dict:
    tuid = f"toolu_fake_{idx:06d}"
    return {
        "type": "user",
        "sessionId": sid,
        "uuid": str(uuid.uuid4()),
        "isSidechain": False,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tuid,
                    "content": f"hello {idx}",
                    "is_error": False,
                }
            ],
        },
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=Path, required=True)
    p.add_argument("--count", type=int, default=50)
    p.add_argument("--rate", type=float, default=5.0, help="events per second")
    p.add_argument("--rotate-at", type=int, default=0, help="rotate file after N events")
    args = p.parse_args()

    args.target.parent.mkdir(parents=True, exist_ok=True)
    sid = str(uuid.uuid4())
    interval = 1.0 / max(args.rate, 0.1)

    with args.target.open("a", encoding="utf-8") as fh:
        fh.write("")

    emitted = 0
    for i in range(args.count):
        with args.target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(make_tool_use(sid, i)) + "\n")
            fh.flush()
        time.sleep(interval / 2)
        with args.target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(make_tool_result(sid, i)) + "\n")
            fh.flush()
        time.sleep(interval / 2)
        emitted += 1
        if args.rotate_at and emitted == args.rotate_at:
            args.target.write_text("")  # truncate
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
