"""Reading `artifacts/` — the console's only source of truth.

Nothing here caches.  A capability's Run button becomes enabled after
approval because the file on disk changed and the next request re-reads
it, not because the console remembers a decision.  That is worth the
handful of file reads per page: state that lives in two places drifts.

An artifact that fails validation is surfaced, not skipped.  A reviewer
looking for a capability they know exists must not be told it does not.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from artifacts.schema import CapabilityArtifact
from control.approval import check_approval, content_hash

ARTIFACTS_DIR = Path(
    os.environ.get("OPERATOR_ARTIFACTS_DIR", "artifacts")
).resolve()

# artifacts/<capability_id>@<major>.json — the established naming convention.
_ARTIFACT_FILE_RE = re.compile(r"^(?P<cap>.+)@(?P<ver>[^@]+)\.json$")


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CatalogEntry:
    """One artifact file, loaded and ready to render."""

    path: Path
    artifact: CapabilityArtifact

    @property
    def capability_id(self) -> str:
        return self.artifact.capability_id

    @property
    def version(self) -> str:
        return self.artifact.version

    @property
    def key(self) -> str:
        """`id@version` — unique across the directory, and URL-safe."""
        return f"{self.capability_id}@{self.version}"

    @property
    def status(self) -> str:
        return self.artifact.approval.status

    @property
    def risk_class(self) -> str:
        return self.artifact.approval.risk_class

    @property
    def derived_risk(self) -> str:
        return self.artifact.derived_risk_class()

    @property
    def risk_disagrees(self) -> bool:
        """Declared risk below derived risk is a review finding, not a detail."""
        return self.risk_class != self.derived_risk

    @property
    def verified_runs(self) -> int | None:
        return self.artifact.meta.verified_runs

    @property
    def runnable(self) -> bool:
        return self.run_blocked_reason is None

    @property
    def run_blocked_reason(self) -> str | None:
        """Why Run is disabled, in the words the system itself uses.

        A draft is not refused by `check_approval()` — draft artifacts pass
        it, because approval enforcement applies only to artifacts claiming
        to be approved.  The engine would still run one in REPLAY mode.  The
        console refuses anyway: "approved" is the gate a capability crosses
        before it runs unattended, and the console is where that gate is
        visible.
        """
        if self.status != "approved":
            return (
                f"Status is {self.status!r}, not 'approved'. A capability "
                f"runs unattended only after a human has approved it "
                f"against a content hash."
            )
        ok, reason = check_approval(self.artifact)
        if not ok:
            return reason
        return None

    @property
    def content_hash(self) -> str:
        return content_hash(self.artifact)


@dataclass(frozen=True)
class BrokenEntry:
    """An artifact file that would not load.  Rendered, never swallowed."""

    path: Path
    error: str

    @property
    def key(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class Catalog:
    entries: list[CatalogEntry]
    broken: list[BrokenEntry]

    @property
    def approved(self) -> list[CatalogEntry]:
        """What the catalog shows: one row per capability an operator can run.

        A presentation filter, not enforcement.  `check_approval()` already
        refuses an unapproved artifact before a step executes; this stops
        the first screen from listing things it would refuse.  `entries`
        stays complete because `resolve()`, `newer_versions()` and the
        review queue all need every artifact, and a bookmark to a draft
        must still explain itself rather than 404.

        Grouped by `capability_id`, newest approved version only.  A
        catalog of what an operator can do should list capabilities, not
        versions — two rows differing only by a version number ask the
        reader to pick, and there is nothing to pick between.  Older
        approved versions stay resolvable by `id@version`.
        """
        newest: dict[str, CatalogEntry] = {}
        for e in self.entries:
            if e.status != "approved":
                continue
            best = newest.get(e.capability_id)
            if best is None or _version_key(e.version) > _version_key(best.version):
                newest[e.capability_id] = e
        return sorted(newest.values(), key=lambda e: e.capability_id)

    def superseded_count(self, entry: CatalogEntry) -> int:
        """How many older approved versions this row stands in front of."""
        return sum(
            1 for e in self.entries
            if e.capability_id == entry.capability_id
            and e.status == "approved"
            and _version_key(e.version) < _version_key(entry.version)
        )

    @property
    def awaiting_review(self) -> list[CatalogEntry]:
        """Not runnable, and someone has to look at them."""
        return [e for e in self.entries if e.status != "approved"]

    @property
    def drafts(self) -> list[CatalogEntry]:
        return [e for e in self.entries if e.status == "draft"]

    @property
    def revoked(self) -> list[CatalogEntry]:
        return [e for e in self.entries if e.status == "revoked"]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_catalog(directory: Path | None = None) -> Catalog:
    """Every artifact in *directory*, newest version of each id first."""
    d = Path(directory) if directory is not None else ARTIFACTS_DIR
    entries: list[CatalogEntry] = []
    broken: list[BrokenEntry] = []

    if not d.is_dir():
        return Catalog(entries=[], broken=[])

    for path in sorted(d.glob("*.json")):
        try:
            artifact = CapabilityArtifact.model_validate_json(path.read_text())
        except Exception as exc:  # pydantic ValidationError, JSON, or IO
            broken.append(
                BrokenEntry(path=path, error=f"{type(exc).__name__}: {exc}")
            )
            continue
        entries.append(CatalogEntry(path=path, artifact=artifact))

    entries.sort(key=lambda e: (e.capability_id, _version_key(e.version)))
    return Catalog(entries=entries, broken=broken)


def _version_key(version: str) -> tuple[int, ...]:
    """`"10.0"` sorts after `"9.0"`, which a string compare gets wrong."""
    try:
        return tuple(int(p) for p in version.split("."))
    except ValueError:
        return (0,)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


class CapabilityNotFound(LookupError):
    """No artifact matches the requested key."""


class AmbiguousCapability(LookupError):
    """More than one artifact matches the requested key.

    Raised rather than resolved.  This resolver used to return
    ``matches[0]``, which meant that when two files claimed the same
    ``id@version`` — trivially possible while two writers disagreed about
    filenames — the console silently picked one and rendered it as though
    it were the only answer.  A tool built to surface silent wrong answers
    is the last place to contain one.
    """


def resolve(key: str, catalog: Catalog | None = None) -> CatalogEntry:
    """Find one artifact by `capability_id` or `capability_id@version`.

    A bare id resolves to the highest version, so the brief's
    `/run/{capability_id}` keeps working once a second version exists.
    An explicit `@version` is exact — a reviewer following a link to a
    specific version must not be silently handed a different one.
    """
    cat = catalog if catalog is not None else load_catalog()

    if "@" in key:
        cap_id, _, version = key.rpartition("@")
        matches = [
            e for e in cat.entries
            if e.capability_id == cap_id and e.version == version
        ]
        if len(matches) > 1:
            raise AmbiguousCapability(
                f"{len(matches)} artifacts claim {key!r}: "
                + ", ".join(str(m.path) for m in matches)
                + ". Two files cannot describe the same capability at the "
                "same version — delete or re-version one."
            )
        if not matches:
            # A version-suffixed key that misses is worth a specific message:
            # the id may exist at other versions.
            available = sorted(
                e.version for e in cat.entries if e.capability_id == cap_id
            )
            if available:
                raise CapabilityNotFound(
                    f"{cap_id!r} has no version {version!r}. "
                    f"Available: {', '.join(available)}"
                )
            raise CapabilityNotFound(f"No artifact with id {cap_id!r}")
        return matches[0]

    matches = [e for e in cat.entries if e.capability_id == key]
    if not matches:
        raise CapabilityNotFound(f"No artifact with id {key!r}")

    # Two files at the same version is ambiguous however the key was
    # written — a bare id resolving to one of them by sort order would be
    # the same silent choice, one step removed.
    by_version: dict[str, list[CatalogEntry]] = {}
    for e in matches:
        by_version.setdefault(e.version, []).append(e)
    dupes = {v: es for v, es in by_version.items() if len(es) > 1}
    if dupes:
        v, es = next(iter(dupes.items()))
        raise AmbiguousCapability(
            f"{len(es)} artifacts claim {key}@{v}: "
            + ", ".join(str(e.path) for e in es)
            + ". Delete or re-version one before invoking by bare id."
        )

    # A bare id means "the capability", and for a caller that means the one
    # it is allowed to run.  Resolving to the newest version regardless of
    # status would point `/run/{capability_id}` at an unapproved draft the
    # moment discovery compiles one — the agent would be handed a refusal
    # for a capability it was approved to use yesterday.  Newest approved
    # first; newest overall only when nothing is approved.
    approved = [e for e in matches if e.status == "approved"]
    pool = approved or matches
    return max(pool, key=lambda e: _version_key(e.version))


def newer_versions(entry: CatalogEntry, catalog: Catalog) -> list[CatalogEntry]:
    """Versions of the same capability above this one.

    A reviewer looking at v1.0 should know v2.0 exists; a caller resolving
    a bare id to the approved v1.0 should know why it did not get v2.0.
    """
    return [
        e for e in catalog.entries
        if e.capability_id == entry.capability_id
        and _version_key(e.version) > _version_key(entry.version)
    ]
