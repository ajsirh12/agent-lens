"""SessionLocator tests — AC2."""

from __future__ import annotations

import os
import time
from pathlib import Path

from agentlens.locator import SessionLocator


def test_find_active_picks_newest_in_slug(tmp_path: Path) -> None:
    cwd = Path("/fake/project/a")
    slug = str(cwd).replace("/", "-")
    projects = tmp_path / "projects"
    slugged = projects / slug
    slugged.mkdir(parents=True)
    older = slugged / "old.jsonl"
    newer = slugged / "new.jsonl"
    older.write_text("{}\n")
    time.sleep(0.01)
    newer.write_text("{}\n")
    # force mtimes
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    result = loc.find_active()
    assert result == newer
    assert loc.chosen_reason == "slug"


def test_find_active_fallback_scans_all_projects(tmp_path: Path) -> None:
    cwd = Path("/fake/project/a")
    projects = tmp_path / "projects"
    other = projects / "-some-other-project"
    other.mkdir(parents=True)
    f = other / "foo.jsonl"
    f.write_text("{}\n")

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    result = loc.find_active()
    assert result == f
    assert loc.chosen_reason == "fallback"


def test_find_active_returns_none_when_empty(tmp_path: Path) -> None:
    cwd = Path("/fake/project/a")
    projects = tmp_path / "projects"
    projects.mkdir()
    loc = SessionLocator(cwd=cwd, projects_root=projects)
    assert loc.find_active() is None
    assert loc.chosen_reason == "none"


def test_find_candidates_returns_all_sorted_newest_first(tmp_path: Path) -> None:
    cwd = Path("/fake/project/a")
    slug = str(cwd).replace("/", "-")
    slugged = tmp_path / "projects" / slug
    slugged.mkdir(parents=True)
    a = slugged / "a.jsonl"
    b = slugged / "b.jsonl"
    c = slugged / "c.jsonl"
    a.write_text("{}\n")
    b.write_text("{}\n")
    c.write_text("{}\n")
    os.utime(a, (1, 1))
    os.utime(b, (3, 3))
    os.utime(c, (2, 2))

    loc = SessionLocator(cwd=cwd, projects_root=tmp_path / "projects")
    cands = loc.find_candidates()
    assert cands == [b, c, a]


def test_find_candidates_empty_when_no_slugged_dir(tmp_path: Path) -> None:
    cwd = Path("/fake/project/a")
    projects = tmp_path / "projects"
    projects.mkdir()
    loc = SessionLocator(cwd=cwd, projects_root=projects)
    assert loc.find_candidates() == []
