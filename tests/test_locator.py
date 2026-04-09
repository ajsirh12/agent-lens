"""SessionLocator tests — AC2."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agentlens.locator import SessionLocator, _norm


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


# ----------------------------------------------------------------------
# Windows / git-bash compatibility: cwd-field match fallback
# ----------------------------------------------------------------------


def _write_jsonl_with_cwd(path: Path, cwd: str, *, extra_lines: int = 0) -> None:
    """Write a JSONL file whose first row has a ``cwd`` field."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "user", "cwd": cwd, "sessionId": "x"})]
    for i in range(extra_lines):
        lines.append(json.dumps({"type": "assistant", "cwd": cwd, "seq": i}))
    path.write_text("\n".join(lines) + "\n")


def test_norm_handles_posix_and_windows_paths() -> None:
    """_norm normalizes slashes and case-folds Windows drive paths."""
    # POSIX stays case-sensitive.
    assert _norm("/Users/limdk/foo") == "/Users/limdk/foo"
    assert _norm("/Users/limdk/foo/") == "/Users/limdk/foo"
    # Windows backslash path normalizes to forward slash and lowercases.
    assert _norm("C:\\Users\\limdk\\foo") == "c:/users/limdk/foo"
    # Mixed separators.
    assert _norm("C:/Users\\limdk/foo") == "c:/users/limdk/foo"
    # Trailing separator stripped.
    assert _norm("C:\\Users\\limdk\\foo\\") == "c:/users/limdk/foo"


def test_find_candidates_falls_back_to_cwd_match_when_slug_misses(
    tmp_path: Path,
) -> None:
    """Simulates Windows/git-bash where the slug doesn't match Claude
    Code's on-disk directory name but the cwd field in the JSONL does.
    """
    cwd = Path("/fake/project/a")
    projects = tmp_path / "projects"
    # Claude Code wrote the session under a DIFFERENT slugged dir name
    # than our locator would compute. Two JSONLs inside, only one whose
    # first row's cwd matches our target.
    other_dir = projects / "some-other-slug-format"
    match_a = other_dir / "match-a.jsonl"
    match_b = other_dir / "match-b.jsonl"
    nomatch = other_dir / "nomatch.jsonl"
    _write_jsonl_with_cwd(match_a, "/fake/project/a")
    _write_jsonl_with_cwd(match_b, "/fake/project/a")
    _write_jsonl_with_cwd(nomatch, "/different/project")
    os.utime(match_a, (1, 1))
    os.utime(match_b, (3, 3))
    os.utime(nomatch, (2, 2))

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    cands = loc.find_candidates()
    assert cands == [match_b, match_a]  # newest-first, nomatch excluded


def test_find_candidates_prefers_slug_fast_path(tmp_path: Path) -> None:
    """When the slugged dir exists, it wins and we do NOT scan further."""
    cwd = Path("/fake/project/a")
    slug = str(cwd).replace("/", "-")
    projects = tmp_path / "projects"
    slugged = projects / slug
    in_slug = slugged / "a.jsonl"
    _write_jsonl_with_cwd(in_slug, "/fake/project/a")
    # Also a cwd-matching file in a different dir — should be ignored
    # because the slug path already returned results.
    other = projects / "other-dir" / "b.jsonl"
    _write_jsonl_with_cwd(other, "/fake/project/a")

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    cands = loc.find_candidates()
    assert cands == [in_slug]


def test_find_active_uses_cwd_match_before_global_fallback(tmp_path: Path) -> None:
    """find_active prefers cwd-match over 'globally newest' fallback."""
    cwd = Path("/fake/project/a")
    projects = tmp_path / "projects"
    # A matching file in a wrong-slug dir (should be picked).
    match_dir = projects / "wrong-slug-for-a"
    match_file = match_dir / "match.jsonl"
    _write_jsonl_with_cwd(match_file, "/fake/project/a")
    os.utime(match_file, (1, 1))
    # A non-matching but globally newer file in another project.
    other_dir = projects / "-other-project"
    other_file = other_dir / "other.jsonl"
    _write_jsonl_with_cwd(other_file, "/other/project")
    os.utime(other_file, (999, 999))

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    result = loc.find_active()
    assert result == match_file
    assert loc.chosen_reason == "cwd-match"


def test_find_active_still_uses_slug_when_it_exists(tmp_path: Path) -> None:
    """Existing slug-happy-path still works — no regression."""
    cwd = Path("/fake/project/a")
    slug = str(cwd).replace("/", "-")
    projects = tmp_path / "projects"
    slugged = projects / slug
    slugged.mkdir(parents=True)
    j = slugged / "one.jsonl"
    j.write_text("{}\n")

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    assert loc.find_active() == j
    assert loc.chosen_reason == "slug"


def test_cwd_match_skips_malformed_and_permission_errors(tmp_path: Path) -> None:
    """Malformed / empty / unreadable JSONLs are silently ignored."""
    cwd = Path("/fake/project/a")
    projects = tmp_path / "projects"
    d = projects / "some-dir"
    d.mkdir(parents=True)
    # empty
    (d / "empty.jsonl").write_text("")
    # malformed JSON
    (d / "broken.jsonl").write_text("this is not json\n")
    # valid but cwd is not a string
    (d / "wrongshape.jsonl").write_text(json.dumps({"cwd": 42}) + "\n")
    # valid and matching
    good = d / "good.jsonl"
    _write_jsonl_with_cwd(good, "/fake/project/a")

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    cands = loc.find_candidates()
    assert cands == [good]


def test_cwd_match_is_case_insensitive_for_windows_drive_paths(
    tmp_path: Path,
) -> None:
    """Windows path comparison folds case so C:\\Users == c:/users."""
    cwd = Path("C:\\Users\\Limdk\\Project")
    projects = tmp_path / "projects"
    d = projects / "any-dir"
    j = d / "session.jsonl"
    # JSONL was written with a lowercased forward-slash cwd.
    _write_jsonl_with_cwd(j, "c:/users/limdk/project")

    loc = SessionLocator(cwd=cwd, projects_root=projects)
    assert loc.find_candidates() == [j]
    assert loc.find_active() == j
    assert loc.chosen_reason == "cwd-match"
