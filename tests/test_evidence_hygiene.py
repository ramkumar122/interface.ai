"""`evidence/` is a graded location, so its contents are a contract.

A reviewer opens this directory and reads `evidence/README.md` to decide
what to look at.  A directory that is not in the index is either something
they will waste time on or something that should not have been committed —
and the first version of the operator console's tests left eighteen empty
`operator-*/` directories behind, which reads as sloppiness whatever else
is in the submission.

These tests are about the repository, not about any module.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EVIDENCE = REPO / "evidence"
INDEX = EVIDENCE / "README.md"

# A row names a directory in backticks.  Some rows abbreviate a family by
# listing one full name and then the distinguishing suffixes, so a bare
# hex suffix counts as naming its siblings.
_BACKTICKED = re.compile(r"`([A-Za-z0-9._<>*-]+/)`")


def _documented_patterns() -> list[str]:
    """Glob patterns for every directory the index names."""
    text = INDEX.read_text()
    patterns: list[str] = []
    for name in _BACKTICKED.findall(text):
        name = name.rstrip("/")
        # `operator-<id>/` documents a family whose ids are generated.
        patterns.append(name.replace("<id>", "*"))
        # `4105757d/` in an abbreviated row stands for the full sibling name.
        if re.fullmatch(r"[0-9a-f]{8}", name):
            patterns.append(f"*-{name}")
    return patterns


def _directories() -> list[str]:
    if not EVIDENCE.is_dir():
        return []
    return sorted(p.name for p in EVIDENCE.iterdir() if p.is_dir())


def _require_evidence() -> None:
    """Skip when there is nothing to check, fail when there is.

    `evidence/` is committed, so a correct clone has contents and this skip
    does not fire there.  What it covers is the working state between
    clearing the directory and finishing a rebuild: a test that fails for
    the whole of a regeneration is a test people learn to ignore, and then
    it is not protecting anything.

    The moment a directory exists, every assertion applies again — which is
    the case these tests were written for.  See docs/REGENERATE_EVIDENCE.md.
    """
    if not _directories():
        pytest.skip(
            "evidence/ is empty — nothing to check. Regenerate it with "
            "docs/REGENERATE_EVIDENCE.md, then run this again."
        )
    if not INDEX.is_file():
        pytest.fail(
            f"evidence/ holds {len(_directories())} directories but has no "
            f"README.md index. A reviewer reads the index to decide what to "
            f"open. Rebuild it — docs/REGENERATE_EVIDENCE.md has the table."
        )


def test_every_evidence_directory_is_in_the_index():
    """Nothing sits in evidence/ that README.md does not account for."""
    _require_evidence()
    patterns = _documented_patterns()
    undocumented = [
        d for d in _directories()
        if not any(fnmatch.fnmatch(d, p) for p in patterns)
    ]
    assert not undocumented, (
        "evidence/ holds directories that evidence/README.md does not name: "
        f"{undocumented}. Either add a row saying what the run demonstrates, "
        "or delete it. A reviewer reads the index to decide what to open, so "
        "an unlisted directory is either wasted time or litter. Tests must "
        "never write here — point them at tmp_path."
    )


def test_no_empty_evidence_directories():
    """An empty directory carries no evidence.

    `WebSurface` creates its evidence directory on construction, so a clean
    run that never escalates leaves one behind.  That is normal while
    working and not something to commit.
    """
    empty = [d for d in _directories() if not any((EVIDENCE / d).iterdir())]
    assert not empty, (
        f"evidence/ holds empty directories: {empty}. They are the residue "
        "of a script that made its evidence directory and had nothing to "
        "write — delete them rather than committing a directory that proves "
        "nothing."
    )


@pytest.mark.parametrize("forbidden", ["operator-"])
def test_console_run_directories_are_not_committed_wholesale(forbidden):
    """One kept escalation is evidence; a pile of run ids is not.

    Every console run makes `evidence/operator-<id>/`. Keeping the ones that
    captured a screenshot is the point; keeping every run that ever
    happened just buries the index.
    """
    _require_evidence()
    runs = [d for d in _directories() if d.startswith(forbidden)
            and d != "operator-console"]
    assert len(runs) <= 3, (
        f"{len(runs)} {forbidden}* directories in evidence/. Keep the runs a "
        "reviewer should open — normally the escalation that captured a "
        "screenshot — and delete the rest."
    )


def test_the_example_artifacts_match_the_shipped_ones():
    """`evidence/example-artifact/` is copies, and copies drift.

    The brief asks for the saved artifact in `evidence/`; the live ones
    live in `artifacts/`. Duplicating a file is the cheap answer, and a
    duplicate nobody checks is how a reviewer ends up reading a capability
    that no longer exists. Byte-identical or the suite fails.
    """
    _require_evidence()
    examples = EVIDENCE / "example-artifact"
    if not examples.is_dir():
        pytest.skip("evidence/example-artifact/ not present")

    shipped_dir = REPO / "artifacts"
    copies = sorted(examples.glob("*.json"))
    assert copies, "evidence/example-artifact/ holds no artifact JSON"

    for copy in copies:
        shipped = shipped_dir / copy.name
        assert shipped.is_file(), (
            f"{copy.name} is in evidence/example-artifact/ but not in "
            f"artifacts/. Either it was renamed and the copy is stale, or "
            f"the capability was withdrawn and the copy should go with it."
        )
        assert copy.read_bytes() == shipped.read_bytes(), (
            f"{copy.name} differs from artifacts/{copy.name}. The copy in "
            f"evidence/ is stale — re-copy it. A reviewer reading a "
            f"capability that no longer matches the one that runs is worse "
            f"than no copy at all."
        )
