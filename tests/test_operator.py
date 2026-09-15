"""Tests for the operator console.

No browser.  The console renders artifacts, results and intervention
requests — all of which are constructible in-process — so everything here
runs in a bare environment.

The renderer tests assert exact sentences.  That looks brittle and is
deliberate: the value of the review screen is that a risk reviewer can
judge a locator from the English, so the English is the contract.  A
change to it should require someone to look at the new sentence and agree
it still says the same thing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from artifacts.schema import (
    Approval,
    CapabilityArtifact,
    Checkpoint,
    CreatedFrom,
    EscalationPolicy,
    FrameRef,
    InputParam,
    Locator,
    Meta,
    NamedRegionRef,
    OutputParam,
    RejectedLocator,
    Step,
    ValueSpec,
)

from operator_console.review import CHECKLIST, checklist_for

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "artifacts" / "coredesk.member.read_savings_balance@1.json"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def shipped() -> CapabilityArtifact:
    return CapabilityArtifact.model_validate_json(SHIPPED.read_text())


def _minimal_artifact(**overrides) -> CapabilityArtifact:
    """A schema-valid artifact with one trivial step, for synthetic cases."""
    base = dict(
        capability_id="coredesk.test.synthetic",
        version="1.0",
        description="A synthetic capability for renderer tests.",
        surface_id="coredesk",
        created_from=CreatedFrom(
            run_id="test-run", model="none", steps_in_transcript=1,
            wall_seconds=0.0,
        ),
        inputs=[],
        outputs=[],
        steps=[
            Step(
                step_id="noop", ordinal=1, intent="Do nothing",
                action="click",
                target=Locator(
                    strategy="ax",
                    frame=FrameRef(match="exact", value="main"),
                    role="button", name="OK",
                ),
            )
        ],
        known_outcomes=[],
        checkpoint=Checkpoint(
            description="done", evidence_refs=[], proposed_by="human",
        ),
        escalation_policy=EscalationPolicy(
            on_stuck="escalate", on_unknown_state="escalate",
            on_blocked="return",
        ),
        approval=Approval(status="draft", risk_class="safe"),
        meta=Meta(schema_version="1.0"),
    )
    base.update(overrides)
    return CapabilityArtifact(**base)


# --------------------------------------------------------------------------- #
# Test 6 — the plain-English renderer, one exact sentence per strategy
# --------------------------------------------------------------------------- #


class TestDescribeLocator:
    """The three strategies, pinned to exact strings."""

    def test_ax(self):
        from operator_console.describe import describe_locator

        loc = Locator(
            strategy="ax",
            frame=FrameRef(match="exact", value="main"),
            role="textbox", name="Member Number",
        )
        assert describe_locator(loc) == ['textbox named "Member Number"']

    def test_ax_scoped(self):
        from operator_console.describe import describe_locator

        loc = Locator(
            strategy="ax_scoped",
            frame=FrameRef(match="exact", value="main"),
            role="button", name="Search",
            within=NamedRegionRef(role="form", name="Member Search"),
        )
        assert describe_locator(loc) == [
            'button named "Search", inside the form "Member Search"'
        ]

    def test_ax_relative(self):
        from operator_console.describe import describe_locator

        loc = Locator(
            strategy="ax_relative",
            frame=FrameRef(match="exact", value="main"),
            role="cell",
            table_name="Share accounts",
            column_header="AVAILABLE",
            row_key="0000",
            row_match="exact",
        )
        assert describe_locator(loc) == [
            'in table "Share accounts",',
            'the row where the first cell is exactly "0000",',
            'the cell under the column header "AVAILABLE"',
        ]

    def test_ax_relative_prefix_match_says_prefixed_by(self):
        """`row_match` changes the claim, so it must change the sentence."""
        from operator_console.describe import describe_locator

        loc = Locator(
            strategy="ax_relative",
            frame=FrameRef(match="exact", value="main"),
            role="cell",
            table_name="Share accounts",
            column_header="AVAILABLE",
            row_key="00",
            row_match="prefix",
        )
        assert 'the row where the first cell is prefixed by "00",' in (
            describe_locator(loc)
        )

    def test_named_frame_is_stated_main_frame_is_not(self):
        from operator_console.describe import describe_frame

        assert describe_frame(FrameRef(match="exact", value="main")) is None
        assert describe_frame(
            FrameRef(
                match="role_name", role="iframe",
                name_prefix="Share accounts for member",
            )
        ) == 'in the frame titled "Share accounts for member…"'


class TestDescribeStepAgainstShippedArtifact:
    """The real artifact, rendered as the review screen will show it."""

    def test_read_step_renders_the_whole_sentence(self, shipped):
        from operator_console.describe import render_step_text

        step = next(s for s in shipped.steps if s.step_id == "read_balance")
        assert render_step_text(step, shipped) == (
            "5. Read the available balance from the primary savings row "
            "(suffix 0000) in the shares table\n"
            '   → in the frame titled "Share accounts for member…",\n'
            '     in table "Share accounts",\n'
            '     the row where the first cell is exactly "0000",\n'
            '     the cell under the column header "AVAILABLE"\n'
            "   → into output: savings_balance (money)"
        )

    def test_type_step_renders_rejected_locator_with_verbatim_reason(
        self, shipped
    ):
        from operator_console.describe import render_step_text

        step = next(s for s in shipped.steps if s.step_id == "type_member_no")
        assert render_step_text(step, shipped) == (
            "2. Type the member number into the search field\n"
            '   → textbox named "Member Number"\n'
            "   → value from input: member_no\n"
            '   ✕ rejected: dom_id "ctl00_MainContent_txtMemberNo"\n'
            "     (ASP.NET WebForms control IDs are unstable across versions "
            "and tenant configurations)"
        )

    def test_intent_prints_verbatim(self, shipped):
        """The renderer describes locators; it never rewrites prose."""
        from operator_console.describe import describe_steps

        described = describe_steps(shipped)
        assert [d.intent for d in described] == [s.intent for s in shipped.steps]

    def test_rejected_count_distinguishes_nothing_to_reject(self, shipped):
        from operator_console.describe import count_rejected

        c = count_rejected(shipped)
        assert c.total == 5
        assert c.with_rejected == 1
        assert c.without == 4
        assert c.sentence == (
            "1 of 5 steps recorded a rejected locator "
            "(4 had no DOM id to reject)"
        )


class TestDescribeValue:
    def test_literal(self):
        from operator_console.describe import describe_value

        art = _minimal_artifact(
            steps=[
                Step(
                    step_id="t", ordinal=1, intent="Type a constant",
                    action="type",
                    target=Locator(
                        strategy="ax",
                        frame=FrameRef(match="exact", value="main"),
                        role="textbox", name="Branch",
                    ),
                    value=ValueSpec(source="literal", literal="001"),
                )
            ]
        )
        assert describe_value(art.steps[0], art) == 'value: "001"'

    def test_no_value_renders_nothing(self, shipped):
        from operator_console.describe import describe_value

        step = next(s for s in shipped.steps if s.step_id == "click_menu")
        assert describe_value(step, shipped) is None


# --------------------------------------------------------------------------- #
# Test 9 — boundaries
# --------------------------------------------------------------------------- #


def _imports(pyfile: Path) -> set[str]:
    tree = ast.parse(pyfile.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                found.add(base)
            for a in node.names:
                found.add(f"{base}.{a.name}" if base else a.name)
    return found


def _py_files(pkg: str) -> list[Path]:
    d = REPO / pkg
    return list(d.rglob("*.py")) if d.exists() else []


def test_operator_does_not_import_coredesk():
    """The console renders artifacts and results, not the target app."""
    for f in _py_files("operator_console"):
        roots = {m.split(".")[0] for m in _imports(f)}
        assert "coredesk" not in roots, f"{f} imports coredesk"


def test_coredesk_does_not_import_operator():
    """Restated here so the console's boundary fails in the console's file."""
    for f in _py_files("coredesk"):
        roots = {m.split(".")[0] for m in _imports(f)}
        assert "operator_console" not in roots, f"{f} imports operator_console"


def test_operator_never_imports_genai():
    """The console is on the replay path.  No model, ever."""
    for f in _py_files("operator_console"):
        mods = _imports(f)
        assert not any(m.startswith("google.genai") for m in mods), f


# --------------------------------------------------------------------------- #
# Tests 1, 2, 3, 7, 8 — the screens, through the routes
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A console pointed at a throwaway artifacts/ directory.

    Approval writes to disk, so the tests that approve must not touch the
    shipped artifact.  `load_catalog()` reads the module global at call
    time, which is what makes this redirection work.
    """
    from fastapi.testclient import TestClient

    import operator_console.app as app_mod
    import operator_console.catalog as catalog_mod
    import operator_console.runner as runner_mod
    from operator_console.app import app

    art_dir = tmp_path / "artifacts"
    art_dir.mkdir()
    monkeypatch.setattr(catalog_mod, "ARTIFACTS_DIR", art_dir)
    # Runs driven by tests must not write into evidence/, which is graded.
    ev_dir = tmp_path / "evidence"
    # exist_ok: an autouse fixture may have created it first.
    ev_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(runner_mod, "EVIDENCE_ROOT", ev_dir)
    # And no test reads the real evidence/ either: it is regenerated, and a
    # suite that depends on its contents fails on a clean checkout.
    monkeypatch.setattr(app_mod, "EVIDENCE_ROOT", ev_dir.resolve())

    tc = TestClient(app)
    tc.artifacts_dir = art_dir  # type: ignore[attr-defined]
    return tc


def _write(art_dir: Path, artifact: CapabilityArtifact) -> Path:
    path = art_dir / f"{artifact.capability_id}@{artifact.version.split('.')[0]}.json"
    path.write_text(artifact.model_dump_json(indent=2))
    return path


def _approved_copy(artifact: CapabilityArtifact) -> CapabilityArtifact:
    from control.approval import approve

    data = artifact.model_dump()
    data["meta"]["verified_runs"] = 2
    return approve(CapabilityArtifact.model_validate(data), "D. PARK — Risk / Ops")


class TestCatalog:
    def test_lists_artifacts_with_status_and_risk(self, client, shipped):
        _write(client.artifacts_dir, shipped)
        body = client.get("/").text

        assert "coredesk.member.read_savings_balance" in body
        assert "approved" in body
        assert "safe" in body
        # From the artifact, not a literal.  The claim is that the catalog
        # renders the count; re-verifying a capability legitimately changes
        # it, and that should not read as a console regression.
        assert f"{shipped.meta.verified_runs} verified runs" in body

    def test_a_draft_is_absent_from_the_catalog(self, client, shipped):
        """The catalog is what an operator can do.

        Drafts used to render here with Run disabled, which put things a
        reviewer must act on in the list of things an operator may invoke.
        They live on /review now.
        """
        draft = CapabilityArtifact.model_validate(
            {**shipped.model_dump(),
             "approval": {"status": "draft", "risk_class": "safe"}}
        )
        _write(client.artifacts_dir, draft)
        body = client.get("/").text

        assert "coredesk.member.read_savings_balance" not in body.split(
            "awaiting review")[-1]
        assert "1 awaiting review" in body
        assert 'href="/review"' in body

    def test_the_capability_page_for_a_draft_still_explains_itself(
        self, client, shipped
    ):
        """A stale link or a bookmark must not 404."""
        draft = CapabilityArtifact.model_validate(
            {**shipped.model_dump(),
             "approval": {"status": "draft", "risk_class": "safe"}}
        )
        _write(client.artifacts_dir, draft)
        r = client.get("/capability/coredesk.member.read_savings_balance@1.0")

        assert r.status_code == 200
        assert "runs unattended only after a human has approved it" in r.text
        assert "/review/coredesk.member.read_savings_balance@1.0" in r.text

    def test_the_header_counts_both_sides_of_the_gate(self, client, shipped):
        _write(client.artifacts_dir, shipped)  # approved
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(
            {**shipped.model_dump(), "version": "2.0",
             "approval": {"status": "draft", "risk_class": "safe"}}
        ))
        body = client.get("/").text
        assert "1 approved" in body
        assert "1 awaiting review" in body

    def test_the_zero_case_reads_as_a_state_not_a_dead_link(
        self, client, shipped
    ):
        """Clicking through to an empty page is worse than not clicking.

        The masthead's Review queue link stays — it always does. What must
        not be a link is the count itself.
        """
        import re

        _write(client.artifacts_dir, shipped)
        body = client.get("/").text
        assert "none awaiting review" in body

        gate = re.search(r'<p class="gate-count">(.*?)</p>', body, re.S)
        assert gate is not None
        assert "<a " not in gate.group(1)

    def test_an_artifact_that_will_not_load_is_shown_not_skipped(self, client):
        (client.artifacts_dir / "broken@1.json").write_text('{"capability_id": 1}')
        body = client.get("/").text

        assert "broken@1.json" in body
        assert "will not load" in body


class TestGeneratedForm:
    """The form comes from `artifact.inputs`, never from a hand-written page."""

    def test_two_input_artifact_renders_both_fields(self, client):
        artifact = _minimal_artifact(
            capability_id="coredesk.test.two_inputs",
            inputs=[
                InputParam(
                    name="member_no", type="string",
                    description="The member number to look up",
                    example="100101",
                ),
                InputParam(
                    name="effective_date", type="date",
                    description="Date in MM/DD/YYYY form",
                    example="09/12/2026",
                ),
            ],
        )
        _write(client.artifacts_dir, _approved_copy(artifact))
        body = client.get("/capability/coredesk.test.two_inputs").text

        for name, type_, desc, example in [
            ("member_no", "string", "The member number to look up", "100101"),
            ("effective_date", "date", "Date in MM/DD/YYYY form", "09/12/2026"),
        ]:
            assert f'name="{name}"' in body
            assert f'id="in_{name}"' in body
            assert type_ in body
            assert desc in body
            assert f'placeholder="{example}"' in body

    def test_every_declared_input_is_required(self, client):
        """The engine has no optional input: `inputs.get(name, "")`."""
        artifact = _minimal_artifact(
            capability_id="coredesk.test.one_input",
            inputs=[
                InputParam(name="member_no", type="string",
                           description="Member number", example="100101")
            ],
        )
        _write(client.artifacts_dir, _approved_copy(artifact))
        body = client.get("/capability/coredesk.test.one_input").text

        assert body.count("required") >= 2  # the label marker and the attribute
        assert "absent input silently becomes an empty string" in body

    def test_declared_outputs_are_shown(self, client, shipped):
        _write(client.artifacts_dir, shipped)
        body = client.get("/capability/coredesk.member.read_savings_balance").text

        assert "savings_balance" in body
        assert "money" in body
        assert "comma grouping" in body


class TestApprovalGate:
    """Server-side, because a `disabled` attribute is a suggestion."""

    @pytest.fixture
    def draft_verified(self, client, shipped):
        data = shipped.model_dump()
        data["approval"] = {"status": "draft", "risk_class": "safe"}
        data["meta"]["verified_runs"] = 2
        art = CapabilityArtifact.model_validate(data)
        _write(client.artifacts_dir, art)
        return art

    def _post(self, client, **form):
        return client.post(
            "/review/coredesk.member.read_savings_balance/decision",
            data={"decision": "approve", **form},
            follow_redirects=False,
        )

    def test_refused_when_checklist_incomplete(self, client, draft_verified):
        r = self._post(
            client, reviewer="D. PARK", role="Risk / Ops",
            checked=["behaviour_matches", "risk_correct"],
        )
        assert r.status_code == 200  # re-rendered, not redirected
        assert "Every assertion must be checked" in r.text
        assert "The declared outcomes are complete" in r.text

        reloaded = CapabilityArtifact.model_validate_json(
            (client.artifacts_dir
             / "coredesk.member.read_savings_balance@1.json").read_text()
        )
        assert reloaded.approval.status == "draft"

    def test_refused_when_name_is_blank(self, client, draft_verified):
        r = self._post(
            client, reviewer="   ", role="Risk / Ops",
            checked=[key for key, _ in CHECKLIST],
        )
        assert r.status_code == 200
        assert "A reviewer name is required." in r.text

    def test_approves_and_records_name_role_and_hash(self, client, draft_verified):
        from control.approval import content_hash

        r = self._post(
            client, reviewer="D. PARK", role="Risk / Ops",
            checked=[key for key, _ in CHECKLIST],
        )
        assert r.status_code == 303

        reloaded = CapabilityArtifact.model_validate_json(
            (client.artifacts_dir
             / "coredesk.member.read_savings_balance@1.json").read_text()
        )
        assert reloaded.approval.status == "approved"
        assert reloaded.approval.approved_by == "D. PARK — Risk / Ops"
        assert reloaded.approval.approved_at is not None
        assert reloaded.approval.content_hash == content_hash(reloaded)

    def test_approving_lands_on_the_catalog_with_it_present(
        self, client, draft_verified
    ):
        """The state change should happen in front of whoever made it."""
        before = client.get("/").text
        assert "coredesk.member.read_savings_balance" not in before.split(
            "awaiting review")[-1]

        r = self._post(client, reviewer="D. PARK", role="Risk / Ops",
                       checked=[key for key, _ in CHECKLIST])
        assert r.status_code == 303
        assert r.headers["location"] == (
            "/?approved=coredesk.member.read_savings_balance@1.0"
        )

        body = client.get(
            "/?approved=coredesk.member.read_savings_balance@1.0"
        ).text
        assert "/capability/coredesk.member.read_savings_balance@1.0" in body
        assert "it can now run unattended" in body
        assert "none awaiting review" in body

    def test_unverified_artifact_is_refused_with_the_reason(self, client, shipped):
        data = shipped.model_dump()
        data["approval"] = {"status": "draft", "risk_class": "safe"}
        data["meta"]["verified_runs"] = None
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(data))

        # The queue offers no button ...
        queue = client.get("/review").text
        assert "blocked" in queue
        assert "verified_runs is not set" in queue

        # ... and the route refuses anyway.
        r = self._post(client, reviewer="D. PARK", role="Risk / Ops",
                       checked=[key for key, _ in CHECKLIST])
        assert r.status_code == 200
        assert "verified_runs is not set" in r.text

    def test_reject_on_a_draft_writes_nothing_and_says_so(
        self, client, draft_verified
    ):
        r = client.post(
            "/review/coredesk.member.read_savings_balance/decision",
            data={"decision": "reject", "reviewer": "D. PARK",
                  "role": "Risk / Ops"},
        )
        assert "Nothing was written" in r.text

        reloaded = CapabilityArtifact.model_validate_json(
            (client.artifacts_dir
             / "coredesk.member.read_savings_balance@1.json").read_text()
        )
        assert reloaded.approval.status == "draft"

    def test_reject_on_an_approved_artifact_revokes_it(self, client, shipped):
        _write(client.artifacts_dir, shipped)
        r = client.post(
            "/review/coredesk.member.read_savings_balance/decision",
            data={"decision": "reject"},
        )
        assert "Approval revoked" in r.text

        reloaded = CapabilityArtifact.model_validate_json(
            (client.artifacts_dir
             / "coredesk.member.read_savings_balance@1.json").read_text()
        )
        assert reloaded.approval.status == "revoked"
        assert reloaded.approval.content_hash is None


class TestResolver:
    """`/run/{capability_id}` keeps working once a second version exists."""

    def test_bare_id_resolves_to_the_newest_version(self, client, shipped):
        v1 = _approved_copy(shipped)
        _write(client.artifacts_dir, v1)
        v2 = CapabilityArtifact.model_validate(
            {**shipped.model_dump(), "version": "2.0"}
        )
        _write(client.artifacts_dir, _approved_copy(v2))

        from operator_console.catalog import load_catalog, resolve

        cat = load_catalog(client.artifacts_dir)
        assert resolve("coredesk.member.read_savings_balance", cat).version == "2.0"
        assert resolve(
            "coredesk.member.read_savings_balance@1.0", cat
        ).version == "1.0"

    def test_missing_version_names_the_ones_that_exist(self, client, shipped):
        _write(client.artifacts_dir, shipped)
        from operator_console.catalog import (
            CapabilityNotFound, load_catalog, resolve,
        )

        cat = load_catalog(client.artifacts_dir)
        with pytest.raises(CapabilityNotFound, match="Available: 1.0"):
            resolve("coredesk.member.read_savings_balance@9.0", cat)


class TestEvidenceRoute:
    def test_serves_a_transcript(self, client, tmp_path):
        d = tmp_path / "evidence" / "discover-test-serves"
        d.mkdir(parents=True)
        (d / "summary.json").write_text('{"status": "done"}')

        r = client.get("/evidence/discover-test-serves/summary.json")
        assert r.status_code == 200
        assert r.json()["status"] == "done"

    def test_refuses_a_path_outside_evidence(self, client):
        r = client.get("/evidence/..%2f..%2fREPORT.md")
        assert r.status_code == 404
        assert "outside evidence/" in r.text


class TestReviewDetailSurfacesGaps:
    """An empty section is a review finding, not an empty table."""

    def test_no_known_outcomes_is_warned_on(self, client):
        artifact = _minimal_artifact(capability_id="coredesk.test.no_outcomes")
        data = artifact.model_dump()
        data["meta"]["verified_runs"] = 1
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(data))

        body = client.get("/review/coredesk.test.no_outcomes").text
        assert "No known outcomes are declared" in body
        assert "you can honestly tick against an empty list" in body

    def test_undetectable_outcome_is_warned_on(self, client):
        """Same rule as `cli review`: an outcome nothing can detect is a
        promise to the caller that nothing keeps."""
        from artifacts.schema import KnownOutcome

        artifact = _minimal_artifact(
            capability_id="coredesk.test.undetectable",
            known_outcomes=[
                KnownOutcome(
                    id="MEMBER_INACTIVE",
                    condition="The member is inactive",
                    business_meaning="Nothing can be done for this member",
                    caller_action="return",
                )
            ],
        )
        data = artifact.model_dump()
        data["meta"]["verified_runs"] = 1
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(data))

        body = client.get("/review/coredesk.test.undetectable").text
        assert "MEMBER_INACTIVE has no detection fields" in body
        assert "cannot detect this outcome at runtime" in body

    def test_evidence_is_not_attributed_to_an_unrelated_capability(self, client):
        """Reports do not name a capability, so they are matched by output
        name.  A capability declaring different outputs gets none."""
        artifact = _minimal_artifact(
            capability_id="coredesk.test.other_outputs",
            outputs=[
                OutputParam(name="card_status", type="text", shape="ACTIVE",
                            description="The card status")
            ],
            steps=[
                Step(
                    step_id="r", ordinal=1, intent="Read the status",
                    action="read",
                    target=Locator(
                        strategy="ax",
                        frame=FrameRef(match="exact", value="main"),
                        role="cell", name="Status",
                    ),
                    value=ValueSpec(source="output", output_name="card_status"),
                )
            ],
        )
        data = artifact.model_dump()
        data["meta"]["verified_runs"] = 1
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(data))

        body = client.get("/review/coredesk.test.other_outputs").text
        assert "74,209.99" not in body  # replay-determinism's savings figures
        assert "no per-run report found in evidence/" in body


class TestBareIdResolvesToWhatMayRun:
    """`/run/{capability_id}` must not start pointing at a draft the moment
    discovery compiles a new version."""

    def test_bare_id_prefers_the_newest_approved_version(self, client, shipped):
        from operator_console.catalog import load_catalog, resolve

        _write(client.artifacts_dir, _approved_copy(shipped))
        draft_v2 = CapabilityArtifact.model_validate(
            {**shipped.model_dump(), "version": "2.0",
             "approval": {"status": "draft", "risk_class": "safe"}}
        )
        _write(client.artifacts_dir, draft_v2)

        cat = load_catalog(client.artifacts_dir)
        chosen = resolve("coredesk.member.read_savings_balance", cat)
        assert chosen.version == "1.0"
        assert chosen.runnable

    def test_falls_back_to_newest_when_nothing_is_approved(self, client, shipped):
        from operator_console.catalog import load_catalog, resolve

        for version in ("1.0", "2.0"):
            _write(client.artifacts_dir, CapabilityArtifact.model_validate(
                {**shipped.model_dump(), "version": version,
                 "approval": {"status": "draft", "risk_class": "safe"}}
            ))

        cat = load_catalog(client.artifacts_dir)
        assert resolve(
            "coredesk.member.read_savings_balance", cat
        ).version == "2.0"

    def test_an_explicit_version_is_never_substituted(self, client, shipped):
        """A reviewer following a link to v2.0 must not be handed v1.0."""
        from operator_console.catalog import load_catalog, resolve

        _write(client.artifacts_dir, _approved_copy(shipped))
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(
            {**shipped.model_dump(), "version": "2.0",
             "approval": {"status": "draft", "risk_class": "safe"}}
        ))

        cat = load_catalog(client.artifacts_dir)
        assert resolve(
            "coredesk.member.read_savings_balance@2.0", cat
        ).version == "2.0"

    def test_the_page_says_why_it_did_not_pick_the_newest(self, client, shipped):
        _write(client.artifacts_dir, _approved_copy(shipped))
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(
            {**shipped.model_dump(), "version": "2.0",
             "approval": {"status": "draft", "risk_class": "safe"}}
        ))

        body = client.get("/capability/coredesk.member.read_savings_balance").text
        assert "A newer version exists" in body
        assert "v2.0 (draft)" in body


# --------------------------------------------------------------------------- #
# Tests 4, 5 — the four result statuses and the retry follow-up
# --------------------------------------------------------------------------- #


def _run_state(shipped, result, *, phase="finished", inputs=None):
    """A settled RunState carrying *result*, with no browser anywhere.

    The result types are plain dataclasses, so every panel is renderable
    in-process.  A panel test that needed Chromium would not be run.
    """
    from operator_console.runner import RunState

    state = RunState(
        run_id="operator-test",
        capability_key=f"{shipped.capability_id}@{shipped.version}",
        capability_id=shipped.capability_id,
        inputs=inputs or {"member_no": "100101"},
        policy_mode="replay",
        artifact=shipped,
    )
    state.phase = phase
    state.result = result
    state.finished_at = state.started_at
    if phase == "escalated":
        state.escalated_at_step = result.request.step_id
    return state


def _render_run(client, state):
    """Register a run with the process runner and fetch its status page."""
    from operator_console.runner import RUNNER

    RUNNER._runs[state.run_id] = state
    return client.get(f"/run/{state.run_id}/status").text


class TestResultPanels:
    """One rendering per row of the result contract's table."""

    def test_success_shows_outputs_and_the_drift_note(self, client, shipped):
        from replay.result import ReplaySuccess, StepTrace

        result = ReplaySuccess(
            outputs={"savings_balance": "12,845.50"},
            steps=[
                StepTrace(step_id="click_menu", locator_strategy="ax",
                          elapsed_ms=120.4),
                StepTrace(step_id="read_balance",
                          locator_strategy="ax_relative", elapsed_ms=42.0),
            ],
        )
        body = _render_run(client, _run_state(shipped, result))

        assert "Success." in body
        assert "12,845.50" in body
        assert "money" in body
        # The rung column is the real per-step record ...
        assert "ax_relative" in body
        # ... and the drift list is empty for a stated reason, not silently.
        assert "none are possible today" in body

    def test_business_outcome_return_reads_as_an_answer(self, client, shipped):
        from replay.result import ReplayBusinessOutcome

        result = ReplayBusinessOutcome(
            outcome_id="MEMBER_NOT_FOUND",
            condition="Search returns zero results",
            message="The member number does not exist in the system",
            caller_action="return",
            at_step="click_search",
        )
        body = _render_run(client, _run_state(shipped, result))

        assert "Answer" in body
        assert "The member number does not exist in the system" in body
        assert "not an error" in body
        assert "Failure at step" not in body

    def test_business_outcome_escalate_renders_its_own_panel(
        self, client, shipped
    ):
        from replay.result import ReplayBusinessOutcome

        result = ReplayBusinessOutcome(
            outcome_id="PERMISSION_DENIED",
            condition="Role does not permit this function",
            message="Your role does not permit this function.",
            caller_action="escalate",
            at_step="click_select",
        )
        body = _render_run(client, _run_state(shipped, result))

        assert "PERMISSION_DENIED" in body
        assert "caller_action: escalate" in body
        assert "Resume" not in body  # no handover happened, so no resume signal

    def test_failure_shows_the_diagnostic(self, client, shipped):
        from replay.result import ReplayFailure, StepTrace

        result = ReplayFailure(
            step_id="read_balance",
            step_intent="Read the cell under column AVAILABLE",
            expected='cell under column "AVAILABLE"',
            observed="column not found; columns are: ['SUFFIX', 'TYPE']",
            locator_rung="ax_relative",
            error="ax_relative: column not found",
            evidence_paths=["evidence/operator-test/001-failure.png"],
            steps=[StepTrace(step_id="click_menu", locator_strategy="ax")],
        )
        body = _render_run(client, _run_state(shipped, result))

        assert "Failure at step read_balance" in body
        assert "column not found" in body
        assert "ax_relative" in body
        assert "/evidence/operator-test/001-failure.png" in body
        # The steps that did complete are still shown.
        assert "click_menu" in body

    def test_escalated_renders_the_intervention_panel(self, client, shipped):
        from control.escalate import build_intervention_request
        from replay.result import ReplayEscalated, StepTrace

        request = build_intervention_request(
            run_id="operator-test",
            capability_id=shipped.capability_id,
            step_id="click_menu",
            step_intent="Click link MBRINQ",
            reason="Your session has expired",
            inputs={"member_no": "100101"},
            detector_id="session_expired",
            screenshot_path="evidence/operator-test/001-escalation.png",
            current_url="http://127.0.0.1:8001/?expired=1",
        )
        result = ReplayEscalated(
            request=request,
            resume_token="operator-test:click_menu:1",
            steps=[StepTrace(step_id="click_menu", locator_strategy="ax")],
        )
        body = _render_run(
            client, _run_state(shipped, result, phase="escalated")
        )

        assert "needs a person" in body
        assert "Please sign in again" in body
        assert "Resume" in body
        # Ownership must be legible on the screen.
        for who in ("AUTOMATION", "HUMAN", "NONE"):
            assert who in body
        # And the human must know where to act.
        assert "Playwright browser window, not here" in body
        assert "operator-test:click_menu:1" in body


class TestRetryWithDifferentInput:
    def test_renders_a_follow_up_field_prefilled_with_what_was_submitted(
        self, client, shipped
    ):
        from replay.result import ReplayBusinessOutcome

        # The shipped artifact's outcomes are both `return`, so this uses a
        # copy whose outcome declares the retry action — the console must
        # branch on the contract, not on which artifact happens to ship.
        data = shipped.model_dump()
        data["known_outcomes"][0]["caller_action"] = "retry_with_different_input"
        artifact = CapabilityArtifact.model_validate(data)

        result = ReplayBusinessOutcome(
            outcome_id="MEMBER_NOT_FOUND",
            condition="Search returns zero results",
            message="No records found",
            caller_action="retry_with_different_input",
            at_step="click_search",
        )
        body = _render_run(
            client,
            _run_state(artifact, result, inputs={"member_no": "999999"}),
        )

        assert "Needs a different input" in body
        # The prompt is the artifact's own business_meaning, not parsed prose.
        assert "The member number does not exist in the system" in body
        assert 'name="member_no"' in body
        assert 'value="999999"' in body
        assert "Run again" in body

    def test_the_prompt_is_the_artifacts_words_not_the_engines(
        self, client, shipped
    ):
        from operator_console import result_view
        from replay.result import ReplayBusinessOutcome

        data = shipped.model_dump()
        data["known_outcomes"][0]["caller_action"] = "retry_with_different_input"
        data["known_outcomes"][0]["business_meaning"] = "Try a different member"
        artifact = CapabilityArtifact.model_validate(data)

        result = ReplayBusinessOutcome(
            outcome_id="MEMBER_NOT_FOUND",
            message="some engine-side text",
            caller_action="retry_with_different_input",
            at_step="click_search",
        )
        view = result_view.build(result, artifact, {"member_no": "999999"})
        assert view.follow_up_prompt == "Try a different member"


class TestBusinessOutcomeAnswerWording:
    """`ReplayBusinessOutcome.message` means two different things.

    `replay/detect.py` sets it to the artifact's `business_meaning` on the
    reclassification path (line 170) and to `f"detected: {id}"` on the
    page-text path (line 300).  Rendering it raw makes "does member 999999
    exist?" answer "detected: MEMBER_NOT_FOUND".  The console renders the
    artifact's own words instead, so both detection paths read the same.
    """

    def test_answer_uses_business_meaning_not_the_detector_string(
        self, shipped
    ):
        """`detect.py` now reports `business_meaning` on both paths, but the
        console still reads the artifact: it is the source of truth, and the
        rendering must survive a result from an engine that disagrees."""
        from operator_console import result_view
        from replay.result import ReplayBusinessOutcome

        result = ReplayBusinessOutcome(
            outcome_id="MEMBER_NOT_FOUND",
            condition="Search returns zero results for the given member_no",
            message="detected: MEMBER_NOT_FOUND",  # the pre-fix engine string
            caller_action="return",
            at_step="click_search",
        )
        view = result_view.build(result, shipped, {"member_no": "999999"})

        assert view.message == "The member number does not exist in the system"
        # What was detected stays visible, distinct from what it means.
        assert view.detected_as == (
            "Search returns zero results for the given member_no"
        )

    def test_falls_back_to_the_engine_message_for_an_undeclared_outcome(
        self, shipped
    ):
        """An outcome the artifact does not declare has no words of its own."""
        from operator_console import result_view
        from replay.result import ReplayBusinessOutcome

        result = ReplayBusinessOutcome(
            outcome_id="SOMETHING_UNDECLARED",
            message="Your role does not permit this function.",
            caller_action="return",
            at_step="click_select",
        )
        view = result_view.build(result, shipped, {})
        assert view.message == "Your role does not permit this function."


class TestRouteShapes:
    """`/run/{key}` and `/run/{id}/resume` share a method and a prefix.

    With a `:path` converter the first is greedy and matches
    `operator-1234/resume` as a capability key, so Resume 404s and the
    escalation loop is silently broken.  Caught by running the live gate,
    not by any unit test, which is why there is now one.
    """

    def test_resume_is_not_shadowed_by_the_run_route(self, client):
        r = client.post("/run/operator-does-not-exist/resume",
                        follow_redirects=False)
        # Reaches the resume handler and reports an unknown run, rather than
        # being parsed as a capability named "operator-does-not-exist/resume".
        assert r.status_code == 404
        assert "No run" in r.text
        assert "No artifact with id" not in r.text

    def test_capability_keys_stay_single_segment(self):
        from operator_console.app import app

        for route in app.routes:
            path = getattr(route, "path", "")
            if "{key" in path:
                assert ":path" not in path, (
                    f"{path} uses a greedy converter; a capability key is "
                    f"one segment and a greedy one shadows sibling routes"
                )


class TestEscalationLoopThroughTheRunner:
    """The whole control transfer, without a browser.

    `tests/test_replay.py` proves the engine's escalate/resume loop against
    `MockSurface`.  This drives the same loop through the console's runner,
    so what is covered is the console's part: that it queues the run, holds
    the escalated result, hands that object back to `engine.resume()`, and
    renders each state.  The live gate covers the browser; this covers the
    wiring, and runs everywhere.
    """

    def _artifact_and_surfaces(self):
        from tests.test_replay import (
            MockSurface, OBS_MENU, OBS_TABLE, _make_artifact, _node, _obs,
        )
        from artifacts.schema import Recoverable

        art = _make_artifact(
            recoverables=[
                Recoverable(
                    trigger="session_expired", max_retries=0,
                    strategy="escalate",
                    trigger_pattern="Your session has expired",
                    recovery_action="escalate",
                ),
            ],
        )
        expired = _obs(
            [_node("e1", "paragraph", "Your session has expired.")],
            url="http://test/",
        )
        mock = MockSurface(observations=[expired])
        return art, mock, (OBS_MENU, OBS_TABLE)

    def _drain(self, runner, state, limit=5.0):
        """Wait for the worker thread to settle this run."""
        import time

        deadline = time.monotonic() + limit
        while time.monotonic() < deadline and state.in_flight:
            time.sleep(0.02)
        return state

    def test_escalate_then_resume_to_success(self, client):
        from control.policy import Mode
        from operator_console.runner import RUNNER
        from replay.result import ReplayEscalated, ReplaySuccess

        art, mock, (obs_menu, obs_table) = self._artifact_and_surfaces()

        # The runner builds its surface through this hook, so no browser and
        # no sign-in — the rest of the path is the real one.
        RUNNER._browser_factory = lambda: mock
        try:
            state = RUNNER.start(
                art, "test@1.0", {"key": "0000"},
                policy_mode=Mode.DISCOVERY,
            )
            self._drain(RUNNER, state)

            assert state.phase == "escalated"
            assert isinstance(state.result, ReplayEscalated)
            assert state.escalated_at_step
            # Ownership is legible while the human holds the browser.
            assert state.owner == "HUMAN"

            body = client.get(f"/run/{state.run_id}/status").text
            assert "needs a person" in body
            assert "Resume" in body

            # The human acts in the browser: the page is usable again.
            mock._observations = [obs_menu, obs_table]
            mock._state = 0
            mock._read_values = {"e22": "412.09"}

            client.post(f"/run/{state.run_id}/resume", follow_redirects=False)
            self._drain(RUNNER, state)

            assert state.phase == "finished"
            assert isinstance(state.result, ReplaySuccess)
            assert state.result.outputs.get("balance") == "412.09"

            body = client.get(f"/run/{state.run_id}/status").text
            assert "Success." in body
            assert "412.09" in body
        finally:
            RUNNER._browser_factory = None
            RUNNER._runs.pop(state.run_id, None)
            RUNNER._active = None

    def test_resume_never_parses_the_token(self, client):
        """The token is opaque by contract: the result object is passed back."""
        from control.policy import Mode
        from operator_console.runner import RUNNER

        art, mock, (obs_menu, obs_table) = self._artifact_and_surfaces()
        RUNNER._browser_factory = lambda: mock
        try:
            state = RUNNER.start(
                art, "test@1.0", {"key": "0000"},
                policy_mode=Mode.DISCOVERY,
            )
            self._drain(RUNNER, state)
            escalated = state.result

            # Corrupting the token must not affect resume — nothing reads it.
            escalated.resume_token = "not-a-token-at-all"
            mock._observations = [obs_menu, obs_table]
            mock._state = 0
            mock._read_values = {"e22": "412.09"}

            client.post(f"/run/{state.run_id}/resume", follow_redirects=False)
            self._drain(RUNNER, state)
            assert state.phase == "finished"
            assert state.result.status == "success"
        finally:
            RUNNER._browser_factory = None
            RUNNER._runs.pop(state.run_id, None)
            RUNNER._active = None

    def test_a_second_run_is_refused_while_one_is_in_flight(self, client):
        """One browser window, one run. Refused with a reason, not queued."""
        from control.policy import Mode
        from operator_console.runner import RUNNER, RunnerBusy

        art, mock, _ = self._artifact_and_surfaces()
        RUNNER._browser_factory = lambda: mock
        try:
            state = RUNNER.start(
                art, "test@1.0", {"key": "0000"}, policy_mode=Mode.DISCOVERY,
            )
            self._drain(RUNNER, state)
            # An escalated run still owns the browser: the human is in it.
            assert state.phase == "escalated"
            with pytest.raises(RunnerBusy, match="waiting for a person"):
                RUNNER.start(
                    art, "test@1.0", {"key": "0000"},
                    policy_mode=Mode.DISCOVERY,
                )
        finally:
            RUNNER._browser_factory = None
            RUNNER._runs.pop(state.run_id, None)
            RUNNER._active = None


# --------------------------------------------------------------------------- #
# Discovery mode
# --------------------------------------------------------------------------- #


def _transcript(tmp_path, run_id, records):
    """Write a transcript in the shape `agent/loop.py` flushes."""
    import json

    d = tmp_path / "evidence" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    return d


def _rec(step, action="click", name="MBRINQ", ok=True, allowed=True,
         hash_changed=True, detail=None, from_input=None, tokens=1000):
    args = {"ref": f"e{step}"}
    if from_input:
        args["from_input"] = from_input
    return {
        "step": step, "obs_hash": f"h{step}", "action": action, "args": args,
        "target_role": "link", "target_name": name,
        "ok": ok, "reason": None if ok else "act_failed", "detail": detail,
        "verdict": {"allowed": allowed, "risk": "safe"},
        "trace": None, "prompt_tokens": tokens, "output_tokens": 20,
        "hash_changed": hash_changed,
    }


class TestDiscoveryForm:
    def test_renders_with_the_natural_language_framing(self, client):
        body = client.get("/discover").text
        assert "Discover a capability" in body
        assert 'name="goal"' in body
        assert "<textarea" in body
        # The architecture should be readable off the screen.
        assert "This screen takes language" in body
        assert "Discovery costs API credits" in body

    def test_disabled_with_a_reason_when_the_key_is_unset(
        self, client, monkeypatch, tmp_path
    ):
        import operator_console.discovery as disc

        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        # `.env` at the repo root is a fallback, so point the lookup away.
        monkeypatch.chdir(tmp_path)

        assert disc.key_missing_reason() is not None
        body = client.get("/discover").text
        assert "GOOGLE_API_KEY is not set" in body
        assert "Discovery is unavailable" in body
        assert "disabled" in body

    def test_a_goal_is_required(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        r = client.post("/discover", data={
            "goal": "  ", "capability_id": "coredesk.test.x",
            "output_name": "out", "output_type": "text", "verify_runs": "2",
        })
        assert "A goal is required" in r.text

    def test_an_output_is_not_required(self, client, monkeypatch):
        """A lock or a navigation returns nothing. The form must not invent one."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        r = client.post("/discover", data={
            "goal": "  ", "capability_id": "coredesk.test.x",
            "verify_runs": "2",
        })
        assert "At least one output is required" not in r.text
        assert "A goal is required" in r.text

    # ---- the second parameter set ------------------------------------- #
    #
    # `verify_artifact` refuses a capability with declared inputs and only
    # one parameter set. The console used not to pass one at all, so every
    # discovery of a real capability spent credits, compiled, and was then
    # refused. These pin the form catching it before the model runs.

    def _ok_form(self, **over):
        data = {
            "goal": "read a balance", "capability_id": "coredesk.test.x",
            "verify_runs": "2",
            "input_name": "member_no",
            "input_value": "100101", "input_value2": "100110",
        }
        data.update(over)
        return data

    def test_two_records_reach_the_discovery_state(self, client, monkeypatch):
        from operator_console.runner import RUNNER, RunnerBusy

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        captured = {}

        def fake_start(state, *, entry_url):
            captured["state"] = state
            raise RunnerBusy("stopped before the browser")

        monkeypatch.setattr(RUNNER, "start_discovery", fake_start)
        client.post("/discover", data=self._ok_form())

        assert captured["state"].inputs == {"member_no": "100101"}
        assert captured["state"].verify_also == [{"member_no": "100110"}]

    def test_a_blank_second_record_is_refused_before_the_model_runs(
        self, client, monkeypatch
    ):
        """The credits assertion: no run is queued."""
        from operator_console.runner import RUNNER, RunnerBusy

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        called = []
        monkeypatch.setattr(
            RUNNER, "start_discovery",
            lambda *a, **k: called.append(1),
        )
        r = client.post("/discover", data=self._ok_form(input_value2="  "))

        assert "needs a value for the second record" in r.text
        assert not called, "a run was queued despite an invalid form"

    def test_an_identical_second_record_is_refused(self, client, monkeypatch):
        from operator_console.runner import RUNNER, RunnerBusy

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        called = []
        monkeypatch.setattr(
            RUNNER, "start_discovery",
            lambda *a, **k: called.append(1),
        )
        r = client.post("/discover", data=self._ok_form(input_value2="100101"))

        assert "must differ from the first" in r.text
        assert not called

    def test_a_capability_with_no_inputs_needs_no_second_record(
        self, client, monkeypatch
    ):
        """`verify_artifact` does not ask for one, so neither does the form."""
        from operator_console.runner import RUNNER, RunnerBusy

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        captured = {}

        def fake_start(state, *, entry_url):
            captured["state"] = state
            raise RunnerBusy("stopped before the browser")

        monkeypatch.setattr(RUNNER, "start_discovery", fake_start)
        r = client.post("/discover", data={
            "goal": "open the menu", "capability_id": "coredesk.test.nav",
            "verify_runs": "2",
        })
        assert "second record" not in r.text
        assert captured["state"].verify_also == []

    def test_the_form_prefills_both_records(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        body = client.get("/discover").text
        assert 'value="100101"' in body
        assert 'value="100110"' in body
        assert "prove determinism, not portability" in body

    def test_verify_runs_help_says_the_count_is_across_both(
        self, client, monkeypatch
    ):
        """2 here becomes verified_runs 3, which reads as a bug unsaid."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        body = client.get("/discover").text
        assert "per record" in body
        assert "so 2 here becomes 3" in body

    def test_add_row_round_trips_without_javascript(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        r = client.post("/discover", data={
            "goal": "keep me", "add_input": "1",
            "input_name": "member_no", "input_value": "100101",
        })
        assert r.text.count('name="input_name"') == 2   # one added
        assert "keep me" in r.text                       # nothing lost
        assert "100101" in r.text


class TestTranscriptRendering:
    """The live log and the recording share one renderer."""

    def test_turns_render_with_declared_input_marked(self, tmp_path):
        from operator_console.transcript_view import read_transcript

        d = _transcript(tmp_path, "discover-test-1", [
            _rec(1),
            _rec(2, action="type", name="Member Number", from_input="member_no"),
        ])
        view = read_transcript(d / "transcript.jsonl")
        assert [t.ordinal for t in view.turns] == [1, 2]
        assert view.turns[1].from_input == "member_no"
        assert view.turns[0].outcome == "ok, page changed"

    def test_a_policy_refusal_is_feedback_not_an_error(self, tmp_path):
        from operator_console.transcript_view import read_transcript

        d = _transcript(tmp_path, "discover-test-2", [
            _rec(1, name="Open Account", allowed=False,
                 detail="irreversible and is blocked in both discovery and replay"),
            _rec(2, name="Continue to Review"),
        ])
        view = read_transcript(d / "transcript.jsonl")
        assert view.turns[0].kind == "refused"
        assert "irreversible" in view.turns[0].detail
        assert view.refusals == 1
        # The run continued after being refused — that is the point.
        assert view.turns[1].kind == "ok"

    def test_a_partial_final_line_is_skipped_not_fatal(self, tmp_path):
        """The loop flushes per turn; a reader can still arrive mid-write."""
        from operator_console.transcript_view import read_transcript

        d = _transcript(tmp_path, "discover-test-3", [_rec(1)])
        (d / "transcript.jsonl").write_text(
            (d / "transcript.jsonl").read_text() + '{"step": 2, "acti'
        )
        view = read_transcript(d / "transcript.jsonl")
        assert len(view.turns) == 1

    def test_tokens_accumulate_across_turns(self, tmp_path):
        from operator_console.transcript_view import read_transcript

        d = _transcript(tmp_path, "discover-test-4",
                        [_rec(1, tokens=1000), _rec(2, tokens=2000)])
        view = read_transcript(d / "transcript.jsonl")
        assert view.total_tokens == 1000 + 20 + 2000 + 20


class TestDiscoveryStatusPage:
    """Every phase, from constructed state.  No model, no browser."""

    def _state(self, client, tmp_path, *, phases=(), pipeline=None,
               written=False, artifact_path=None, error=None, records=None):
        from operator_console.discovery import DiscoveryState
        from operator_console.runner import RUNNER, RunState

        run_id = "discover-test-status"
        d = _transcript(tmp_path, run_id, records or [_rec(1), _rec(2)])
        ds = DiscoveryState(
            goal="read a member's savings balance",
            capability_id="coredesk.member.read_savings_balance",
            model="gemini-3.6-flash",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
            verify_runs=2, evidence_dir=d,
        )
        ds.phases = list(phases)
        ds.pipeline = pipeline
        ds.written = written
        ds.artifact_path = artifact_path
        ds.error = error

        state = RunState(
            run_id=run_id, capability_key=ds.capability_id,
            capability_id=ds.capability_id, inputs=ds.inputs,
            policy_mode="discovery", artifact=None,
            kind="discovery", discovery=ds,
        )
        RUNNER._runs[run_id] = state
        return state

    def test_running_shows_turns_and_no_invented_denominator(
        self, client, tmp_path
    ):
        state = self._state(client, tmp_path)
        state.phase = "running"
        body = client.get(f"/run/{state.run_id}/status").text

        assert "Discovering" in body
        assert "gemini-3.6-flash" in body
        assert "MBRINQ" in body
        assert 'http-equiv="refresh"' in body
        assert "the budget is 25 and the model stops" in body
        assert "of 6" not in body and "of 25" not in body

    def test_complete_and_written_offers_the_review_link(self, client, tmp_path):
        from agent.pipeline import Phase, PipelineResult
        from agent.loop import DiscoveryResult

        art = CapabilityArtifact.model_validate_json(SHIPPED.read_text())
        pipe = PipelineResult(
            result=DiscoveryResult(
                status="done", steps=6, outputs_filled={"savings_balance": "12,845.50"},
                transcript=[], prompt_tokens=13978, output_tokens=162,
                wall_seconds=10.8,
            ),
            artifact=art, verified=True, verify_runs=2,
        )
        state = self._state(
            client, tmp_path, pipeline=pipe, written=True,
            artifact_path="artifacts/x@1.json",
            phases=[Phase("compiling", art.capability_id),
                    Phase("verifying", "2 runs"),
                    Phase("done", "verified 2/2 — outputs matched")],
        )
        state.phase = "finished"
        body = client.get(f"/run/{state.run_id}/status").text

        assert "Written as DRAFT" in body
        assert "unattended until a human approves it" in body
        assert f"/review/{art.capability_id}@{art.version}" in body
        assert "verified 2/2" in body

    def test_verification_failure_names_the_step_and_says_nothing_was_written(
        self, client, tmp_path
    ):
        from agent.loop import DiscoveryResult
        from agent.pipeline import Phase, PipelineResult
        from replay.verify import VerifyResult

        art = CapabilityArtifact.model_validate_json(SHIPPED.read_text())
        pipe = PipelineResult(
            result=DiscoveryResult(
                status="done", steps=6, outputs_filled={"savings_balance": "12,845.50"},
                transcript=[], prompt_tokens=1, output_tokens=1, wall_seconds=1.0,
            ),
            artifact=art,
            verify_result=VerifyResult(
                passed=False,
                failure_summary="run 1/2: output 'savings_balance' = '640.00', expected '12,845.50'",
            ),
            verified=False, verify_runs=2,
        )
        state = self._state(
            client, tmp_path, pipeline=pipe,
            phases=[Phase("verifying", "2 runs"),
                    Phase("failed", "run 1/2: output mismatch", ok=False)],
        )
        state.phase = "finished"
        body = client.get(f"/run/{state.run_id}/status").text

        assert "No artifact was written" in body
        assert "savings_balance" in body
        assert "This is a gate refusing, not a crash" in body
        assert "Review and approve" not in body

    def test_compilation_failure_renders_its_diagnostic(self, client, tmp_path):
        from agent.loop import DiscoveryResult
        from agent.pipeline import Phase, PipelineResult

        diag = ('step 9: cannot locate cell "LOCKED" in frame "main": its '
                'accessible name is the value it reads')
        pipe = PipelineResult(
            result=DiscoveryResult(
                status="done", steps=11, outputs_filled={"card_status": "LOCKED"},
                transcript=[], prompt_tokens=1, output_tokens=1, wall_seconds=1.0,
            ),
            compile_error=diag,
        )
        state = self._state(
            client, tmp_path, pipeline=pipe,
            phases=[Phase("failed", diag, ok=False)],
        )
        state.phase = "finished"
        body = client.get(f"/run/{state.run_id}/status").text

        assert "No artifact was written" in body
        assert "accessible name is the value it reads" in body
        assert "/transcript.jsonl" in body
        assert "This is a gate refusing, not a crash" in body

    def test_the_model_giving_up_is_reported_as_such(self, client, tmp_path):
        from agent.loop import DiscoveryResult
        from agent.pipeline import PipelineResult

        pipe = PipelineResult(
            result=DiscoveryResult(
                status="give_up", steps=4, outputs_filled={}, transcript=[],
                prompt_tokens=1, output_tokens=1, wall_seconds=1.0,
            ),
        )
        state = self._state(client, tmp_path, pipeline=pipe)
        state.phase = "finished"
        body = client.get(f"/run/{state.run_id}/status").text
        assert "give_up" in body
        assert "No artifact was written" in body
        assert "This is a gate refusing, not a crash" in body

    def test_a_crash_is_not_dressed_up_as_a_gate_refusing(
        self, client, tmp_path
    ):
        """Playwright failed to launch, so no gate ever ran.

        The console printed "This is a gate refusing, not a crash" under
        every unwritten outcome, which over the top of a crash is exactly
        backwards — the one tool built to make wrong things visible,
        asserting the system was working as designed.
        """
        crash = ("Error: BrowserType.launch: Executable doesn't exist at "
                 "/var/folders/jk/T/cursor-sandbox-cache/playwright/"
                 "chromium-1234/chrome-mac-arm64/Google Chrome for Testing")
        state = self._state(client, tmp_path, error=crash)
        state.phase = "error"
        state.error = crash
        body = client.get(f"/run/{state.run_id}/status").text

        assert "No artifact was written" in body
        assert "BrowserType.launch" in body
        assert "gate refusing" not in body
        # The console knew what happened, so it must not fall back to the
        # vaguer sentence it used when nothing had set the error.
        assert "ended before the pipeline reported" not in body

    def test_paused_shows_run_step_and_handoff(self, client, tmp_path):
        state = self._state(client, tmp_path)
        state.phase = "paused"
        state.discovery.pause_reason = (
            "the model repeated the same action on the same page three times"
        )
        body = client.get(f"/run/{state.run_id}/status").text

        assert "Paused" in body
        assert "three times" in body
        assert "Run this step" in body
        assert "Hand off to model" in body
        assert 'action="/run/%s/human-step"' % state.run_id in body
        assert 'action="/run/%s/handoff"' % state.run_id in body
        assert 'http-equiv="refresh"' not in body
        assert "No artifact was written" not in body


class TestRehearsalMode:
    RUN = "discover-test-rehearsal"

    @pytest.fixture(autouse=True)
    def _recording(self, tmp_path):
        """A recording the test owns, rather than whatever is in evidence/."""
        _transcript(tmp_path, self.RUN, [
            _rec(1), _rec(2, action="type", name="Member Number",
                          from_input="member_no"),
            _rec(3), _rec(4),
        ])
        (tmp_path / "evidence" / self.RUN / "summary.json").write_text(
            '{"status": "done", "compiled": true, "verified": true}'
        )

    def test_renders_a_saved_transcript_and_says_it_is_a_recording(self, client):
        r = client.get(f"/discover/rehearse?run_id={self.RUN}&step=3")
        assert r.status_code == 200
        assert "Replayed recording" in r.text
        assert "No model was called and no browser is running" in r.text
        # Sliced to the requested step.
        assert "turn 3 of" in r.text.replace("TURN", "turn").lower()

    def test_it_never_says_something_is_thinking(self, client):
        r = client.get(f"/discover/rehearse?run_id={self.RUN}&step=2")
        assert "thinking" not in r.text
        assert "more turns in the recording" in r.text

    def test_refuses_a_path_outside_evidence(self, client):
        r = client.get("/discover/rehearse?run_id=..%2f..%2fartifacts")
        assert r.status_code == 404

    def test_the_picker_lists_what_each_recording_shows(self, client):
        body = client.get("/discover/rehearse").text
        assert self.RUN in body
        assert "status done" in body


class TestDiscoveryHoldsTheBrowser:
    def test_a_replay_started_during_discovery_is_refused(
        self, client, shipped, tmp_path
    ):
        from control.policy import Mode
        from operator_console.runner import RUNNER, RunnerBusy, RunState

        state = RunState(
            run_id="discover-holding", capability_key="x", capability_id="x",
            inputs={}, policy_mode="discovery", artifact=None,
            kind="discovery", discovery=None,
        )
        state.phase = "running"
        RUNNER._runs[state.run_id] = state
        RUNNER._active = state.run_id
        try:
            with pytest.raises(RunnerBusy, match="one job at a time|in flight"):
                RUNNER.start(
                    shipped, "x@1.0", {"member_no": "100101"},
                    policy_mode=Mode.REPLAY,
                )
        finally:
            RUNNER._runs.pop(state.run_id, None)
            RUNNER._active = None


class TestConsoleModelBoundary:
    """`discovery.py` is the one console module allowed to reach the SDK."""

    def test_only_discovery_imports_genai(self):
        for f in _py_files("operator_console"):
            if f.name == "discovery.py":
                continue
            mods = _imports(f)
            assert not any(m.startswith("google.genai") for m in mods), (
                f"{f} imports google.genai. Discovery lives in "
                f"operator_console/discovery.py precisely so every other "
                f"console module stays on the replay path, where there is "
                f"no model."
            )

    def test_the_runner_stays_model_free(self):
        """Both job types share it, so a stray import here would put the SDK
        on the replay path."""
        mods = _imports(REPO / "operator_console" / "runner.py")
        assert not any(m.startswith("google.genai") for m in mods)
        assert not any(m.startswith("agent.brain") for m in mods)


class TestProgressSurfaceMatchesTheProtocol:
    """A decorating surface must accept everything a caller passes.

    `Surface.observe()` declared no arguments while `agent/loop.py` calls
    `observe(screenshot=...)` — rung 3 of the self-correction ladder.  Every
    real implementation accepted it; only the protocol did not say so.  The
    wrapper was written against the declaration and raised `TypeError` on
    the first discovery run that asked for a screenshot.
    """

    def test_observe_forwards_the_screenshot_flag(self):
        from operator_console.runner import ProgressSurface

        seen = {}

        class Inner:
            def observe(self, screenshot: bool = False):
                seen["screenshot"] = screenshot
                from surface.base import Observation
                return Observation(
                    url="http://test/", title="t", frames={}, nodes=[],
                    screenshot=None, warnings=[], hash="h",
                )

        s = ProgressSurface(Inner())
        s.observe(screenshot=True)
        assert seen["screenshot"] is True
        s.observe()
        assert seen["screenshot"] is False

    def test_the_wrapper_accepts_every_protocol_argument(self):
        """Signature-compare the decorator against the protocol, so the next
        parameter added to `Surface` cannot silently break the wrapper."""
        import inspect

        from operator_console.runner import ProgressSurface
        from surface.base import Surface

        for name in ("observe", "act", "evidence", "release", "reacquire"):
            proto = inspect.signature(getattr(Surface, name))
            wrapper = inspect.signature(getattr(ProgressSurface, name))
            assert list(proto.parameters) == list(wrapper.parameters), (
                f"ProgressSurface.{name}{wrapper} does not match "
                f"Surface.{name}{proto}"
            )


# --------------------------------------------------------------------------- #
# The checklist teaches, and the source is the file
# --------------------------------------------------------------------------- #


class TestChecklistExplanations:
    """A risk reviewer who is not an engineer cannot honestly tick item 2
    without knowing the failure mode.  The reasoning is drawn from the
    artifact in front of them, not from a constant."""

    def test_every_item_renders_its_reasoning(self, client, shipped):
        data = shipped.model_dump()
        data["approval"] = {"status": "draft", "risk_class": "safe"}
        data["meta"]["verified_runs"] = 2
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(data))

        body = client.get("/review/coredesk.member.read_savings_balance@1.0").text
        for _, label, why in checklist_for(shipped):
            assert label in body
            # The first clause is enough; the template may wrap the rest.
            assert why.split(".")[0] in body

    def test_it_quotes_this_artifacts_own_read_step(self, shipped):
        why = dict(
            (k, w) for k, _, w in checklist_for(shipped)
        )["no_record_dependent_locator"]

        assert "Step 5" in why
        assert '"AVAILABLE"' in why
        assert '"0000"' in why
        assert "the compiler refuses to emit one" in why

    def test_a_capability_with_no_read_step_still_reads_correctly(self):
        """Card lock writes and reads nothing — the concern is every
        locator, not only reads, so the sentence must not imply otherwise."""
        artifact = _minimal_artifact(
            capability_id="coredesk.card.lock",
            steps=[
                Step(step_id="click_lock", ordinal=1,
                     intent='Click radio "Lock"', action="click",
                     risk="guarded_write",
                     target=Locator(
                         strategy="ax",
                         frame=FrameRef(match="exact", value="main"),
                         role="radio", name="Lock")),
            ],
        )
        why = dict(
            (k, w) for k, _, w in checklist_for(artifact)
        )["no_record_dependent_locator"]

        assert "Every step is found by role and name" in why
        assert "Step " not in why          # no step quoted; there is no read
        assert "reads" not in why          # and nothing implies there is one

    def test_counts_come_from_the_artifact(self):
        from artifacts.schema import KnownOutcome

        artifact = _minimal_artifact(
            capability_id="coredesk.test.counts",
            known_outcomes=[
                KnownOutcome(id="ONLY_ONE", condition="c",
                             business_meaning="m", caller_action="return",
                             page_text_pattern="x"),
            ],
        )
        items = dict((k, w) for k, _, w in checklist_for(artifact))
        assert "1 outcome declared (ONLY_ONE)" in items["outcomes_complete"]
        assert "1 safe" in items["risk_correct"]
        assert "the 1 steps" in items["behaviour_matches"]

    def test_no_declared_outcomes_is_said_plainly(self):
        artifact = _minimal_artifact(capability_id="coredesk.test.none")
        items = dict((k, w) for k, _, w in checklist_for(artifact))
        assert "No outcomes are declared" in items["outcomes_complete"]


class TestArtifactSourceIsTheFile:
    def test_the_block_is_the_bytes_on_disk_not_a_reserialisation(
        self, client, shipped
    ):
        """A reviewer who hashes what this block shows must get the stored
        hash.  `model_dump_json()` would reorder keys and reindent, and the
        hash would not match."""
        import json

        data = shipped.model_dump()
        data["approval"] = {"status": "draft", "risk_class": "safe"}
        data["meta"]["verified_runs"] = 2
        art = CapabilityArtifact.model_validate(data)

        # Deliberately unusual on-disk shape: reversed keys, 4-space indent.
        raw = json.dumps(
            dict(reversed(list(json.loads(art.model_dump_json()).items()))),
            indent=4,
        )
        path = client.artifacts_dir / "coredesk.member.read_savings_balance@1.json"
        path.write_text(raw)

        body = client.get("/review/coredesk.member.read_savings_balance@1.0").text

        import html
        import re
        block = re.search(r"<details class=\"source\">.*?<pre>(.*?)</pre>",
                          body, re.S)
        assert block is not None
        assert html.unescape(block.group(1)) == raw
        assert raw != art.model_dump_json(indent=2)  # the shapes really differ

    def test_it_is_not_called_the_llm_output(self, client, shipped):
        """The model emitted function calls; the artifact is what the
        compiler made of them.  The architecture is that distinction."""
        _write(client.artifacts_dir, shipped)
        body = client.get("/review/coredesk.member.read_savings_balance@1.0").text

        assert "the compiled capability, what replay executes" in body
        assert "LLM output" not in body


class TestTranscriptLink:
    def test_it_resolves_from_created_from_run_id(
        self, client, shipped, tmp_path
    ):
        run_id = shipped.created_from.run_id
        _transcript(tmp_path, run_id, [_rec(1)])
        _write(client.artifacts_dir, shipped)
        body = client.get("/review/coredesk.member.read_savings_balance@1.0").text

        assert f"/evidence/{run_id}/transcript.jsonl" in body
        assert "The raw model run this was compiled from" in body

    def test_an_absent_directory_says_so_instead_of_linking(
        self, client, shipped
    ):
        data = shipped.model_dump()
        data["created_from"]["run_id"] = "discover-gone-1234"
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(data))

        body = client.get("/review/coredesk.member.read_savings_balance@1.0").text
        assert "No transcript on disk" in body
        assert "/evidence/discover-gone-1234/transcript.jsonl" not in body


def test_checklist_text_carries_no_markup(shipped):
    """These strings are autoescaped into the template, so markdown
    emphasis would render as literal asterisks on the page."""
    for _, label, why in checklist_for(shipped):
        # Not underscores: `guarded_write` is an identifier, not emphasis.
        for markup in ("*", "<", "`"):
            assert markup not in why, f"{markup!r} in: {why}"
        assert "*" not in label


class TestCatalogListsCapabilitiesNotVersions:
    def test_two_approved_versions_produce_one_row(self, client, shipped):
        _write(client.artifacts_dir, _approved_copy(shipped))
        v2 = CapabilityArtifact.model_validate(
            {**shipped.model_dump(), "version": "2.0"}
        )
        _write(client.artifacts_dir, _approved_copy(v2))

        body = client.get("/").text
        assert body.count('class="id">coredesk.member.read_savings_balance<') == 1
        # ...and it is the newer one.
        assert "/capability/coredesk.member.read_savings_balance@2.0" in body
        assert "1 earlier version" in body

    def test_the_older_version_is_still_reachable_by_key(self, client, shipped):
        _write(client.artifacts_dir, _approved_copy(shipped))
        _write(client.artifacts_dir, _approved_copy(
            CapabilityArtifact.model_validate(
                {**shipped.model_dump(), "version": "2.0"})
        ))
        r = client.get("/capability/coredesk.member.read_savings_balance@1.0")
        assert r.status_code == 200

    def test_one_version_says_nothing_about_earlier_ones(self, client, shipped):
        _write(client.artifacts_dir, _approved_copy(shipped))
        assert "earlier version" not in client.get("/").text


class TestLiteralValuesAreFlaggedForReview:
    """`ValueSpec.review_required` was set by the compiler and rendered
    nowhere. It is the schema's marker for "a human should look at this",
    and it is what should have caught `card.lock` freezing its reason."""

    def _artifact_with_literal(self):
        return _minimal_artifact(
            capability_id="coredesk.test.literal",
            steps=[
                Step(
                    step_id="select_reason", ordinal=1,
                    intent="Choose a reason", action="select",
                    target=Locator(
                        strategy="ax",
                        frame=FrameRef(match="exact", value="main"),
                        role="combobox", name="Reason"),
                    value=ValueSpec(source="literal", literal="Suspected fraud",
                                    review_required=True),
                ),
            ],
        )

    def test_the_renderer_flags_it(self):
        from operator_console.describe import describe_step, render_step_text

        art = self._artifact_with_literal()
        assert describe_step(art.steps[0], art).value_needs_review is True
        assert "literal — review this" in render_step_text(art.steps[0], art)

    def test_an_input_backed_value_is_not_flagged(self, shipped):
        from operator_console.describe import describe_step

        step = next(s for s in shipped.steps if s.step_id == "type_member_no")
        assert describe_step(step, shipped).value_needs_review is False

    def test_the_review_screen_shows_it(self, client):
        art = self._artifact_with_literal()
        data = art.model_dump()
        data["meta"]["verified_runs"] = 1
        _write(client.artifacts_dir, CapabilityArtifact.model_validate(data))

        body = client.get("/review/coredesk.test.literal").text
        assert "literal — review this" in body


class TestDiscoveryFormDefaults:
    """Defaults for the common case, never constraints."""

    def test_output_type_is_a_select_of_engine_coercible_types(self, client):
        """The options must match what `_coerce_output` actually validates.

        Only `money` is checked — everything else passes through — so a
        third option like `number` would name a validation that does not
        exist, and an output labelled `number` would accept "OPEN" exactly
        as `text` does.
        """
        import inspect

        from operator_console.app import OUTPUT_TYPES
        from replay import engine

        body = client.get("/discover").text
        assert '<select name="output_type"' not in body

        body = client.post("/discover", data={"add_output": "1"}).text
        assert '<select name="output_type"' in body
        for t in OUTPUT_TYPES:
            assert f'<option value="{t}"' in body

        # Every offered type is one the engine names, and no type the engine
        # names is missing.
        src = inspect.getsource(engine._coerce_output)
        coerced = {t for t in ("money", "number", "date", "int")
                   if f'== "{t}"' in src}
        assert coerced <= set(OUTPUT_TYPES), (
            f"the engine coerces {coerced - set(OUTPUT_TYPES)} and the form "
            f"does not offer it"
        )
        assert "number" not in OUTPUT_TYPES, (
            "the engine does not coerce 'number'; offering it would promise "
            "a check that does not happen"
        )

    def test_it_renders_prefilled_for_the_common_case(self, client):
        body = client.get("/discover").text
        assert 'value="member_no"' in body
        assert 'value="100101"' in body
        assert 'value="savings_balance"' not in body
        assert 'name="output_name"' not in body
        assert "None declared." in body

    def test_the_prefill_is_a_default_not_a_constraint(self, client, monkeypatch):
        """A capability keyed on something else entirely must be expressible."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        r = client.post("/discover", data={
            "goal": "", "capability_id": "",       # force a re-render
            "input_name": "card_id", "input_value": "CRD-100101-1",
            "output_name": "card_status", "output_type": "text",
        })
        assert 'value="card_id"' in r.text
        assert 'value="CRD-100101-1"' in r.text
        assert 'value="card_status"' in r.text
        assert '<option value="text" selected>' in r.text
        # The defaults are gone, not merged in.
        assert 'value="member_no"' not in r.text
        assert 'value="savings_balance"' not in r.text

    def test_adding_a_second_input_row_keeps_what_was_typed(self, client, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        r = client.post("/discover", data={
            "goal": "lock a card", "add_input": "1",
            "input_name": "member_no", "input_value": "100101",
            "output_name": "card_status", "output_type": "text",
        })
        assert r.text.count('name="input_name"') == 2
        assert "lock a card" in r.text
        assert 'value="100101"' in r.text

    def test_adding_a_second_output_row_defaults_to_a_coercible_type(
        self, client, monkeypatch
    ):
        from operator_console.app import OUTPUT_TYPES

        monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")
        r = client.post("/discover", data={
            "goal": "g", "add_output": "1",
            "output_name": "card_status", "output_type": "text",
        })
        assert r.text.count('<select name="output_type"') == 2
        assert OUTPUT_TYPES[0] == "money"


class TestWorkerErrorReporting:
    """A crashed job leaves two state objects; they must agree."""

    def test_the_worker_mirrors_its_exception_onto_the_discovery_state(
        self, tmp_path
    ):
        import time as _time

        from operator_console.discovery import DiscoveryState
        from operator_console.runner import Runner, RunState

        ds = DiscoveryState(
            goal="g", capability_id="c", model="m", inputs={}, outputs={},
            verify_runs=0, evidence_dir=tmp_path,
        )
        state = RunState(
            run_id="discover-crash", capability_key="c", capability_id="c",
            inputs={}, policy_mode="discovery", artifact=None,
            kind="discovery", discovery=ds,
        )
        runner = Runner()
        runner._runs[state.run_id] = state
        runner._active = state.run_id

        def job():
            raise RuntimeError("BrowserType.launch: Executable doesn't exist")

        job.run_id = state.run_id

        runner._ensure_thread()
        runner._jobs.put(job)

        deadline = _time.monotonic() + 5.0
        while _time.monotonic() < deadline and state.phase != "error":
            _time.sleep(0.01)

        assert state.phase == "error"
        assert "BrowserType.launch" in state.error
        # The status page reads the discovery's state, not the run's.
        assert ds.error == state.error
        assert "BrowserType.launch" in ds.failure_reason
        assert ds.gate_refused is False


class TestVerifyAlsoReachesTheGate:
    """The link that was missing: form -> state -> compile_and_verify.

    `verify_artifact` refuses a capability with declared inputs and no
    second parameter set. Collecting one in the form is useless if
    `complete_discovery` does not pass it on, and that is exactly the way
    this broke — the console spent credits, compiled, and was refused.
    """

    def _state(self, tmp_path, also):
        from operator_console.discovery import DiscoveryState

        return DiscoveryState(
            goal="g", capability_id="coredesk.test.x", model="m",
            inputs={"member_no": "100101"}, outputs={"bal": "money"},
            verify_runs=2, evidence_dir=tmp_path, verify_also=also,
        )

    def test_complete_discovery_forwards_the_second_set(
        self, tmp_path, monkeypatch
    ):
        import operator_console.discovery as disc

        seen = {}

        def fake(result, **kw):
            seen.update(kw)

            class _P:
                artifact = None
                verified = False

                def summary(self, **k):
                    return {}

            return _P()

        monkeypatch.setattr(disc, "compile_and_verify", fake)
        disc.complete_discovery(
            self._state(tmp_path, [{"member_no": "100110"}]), object()
        )

        assert seen["verify_also"] == [{"member_no": "100110"}]

    def test_records_described_is_the_portability_claim(self, tmp_path):
        ds = self._state(tmp_path, [{"member_no": "100110"}])
        assert ds.records_described == "member 100101 and member 100110"

    def test_records_described_groups_multi_input_records(self, tmp_path):
        from operator_console.discovery import DiscoveryState

        ds = DiscoveryState(
            goal="g", capability_id="c", model="m",
            inputs={"member_no": "100101", "city": "Tempe"},
            outputs={}, verify_runs=2, evidence_dir=tmp_path,
            verify_also=[{"member_no": "100114", "city": "Portland"}],
        )
        # " / " binds tighter than " and ", so this does not read as one
        # flat list of four things.
        assert ds.records_described == (
            "member 100101 / city Tempe and member 100114 / city Portland"
        )
