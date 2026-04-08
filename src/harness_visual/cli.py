"""harness-visual CLI entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness-visual",
        description="Live-tail TUI for Claude Code sessions + OMC team state.",
    )
    p.add_argument("--session", type=Path, default=None, help="Path to a JSONL session file.")
    p.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project cwd used to compute the session slug.",
    )
    p.add_argument(
        "--self-test",
        action="store_true",
        help="Render once then exit 0 (CI smoke test).",
    )
    p.add_argument(
        "--no-attach",
        action="store_true",
        help="Don't attach to any session (for tests).",
    )
    p.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Override .omc/state directory.",
    )
    p.add_argument(
        "--latest",
        action="store_true",
        help="Skip the session picker and auto-attach to the newest JSONL.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Import lazily so --help works without textual installed.
    from .app import HarnessVisualApp

    app = HarnessVisualApp(
        session_override=args.session,
        project_root=args.project_root,
        state_dir_override=args.state_dir,
        self_test=args.self_test,
        no_attach=args.no_attach,
        auto_latest=args.latest,
    )
    result = app.run()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
