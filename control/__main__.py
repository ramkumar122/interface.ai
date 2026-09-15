"""CLI for artifact approval and review.

Usage:
    python -m control review  artifacts/coredesk.member.read_savings_balance@1.json
    python -m control approve artifacts/coredesk.member.read_savings_balance@1.json --approver "D. PARK"
    python -m control revoke  artifacts/coredesk.member.read_savings_balance@1.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from artifacts.schema import CapabilityArtifact
from control.approval import (
    ApprovalIntegrityError,
    ApprovalPreconditionError,
    approve,
    content_hash,
    review_checklist,
    revoke,
    write_artifact,
)


def cmd_review(args: argparse.Namespace) -> int:
    path = Path(args.artifact)
    art = CapabilityArtifact.model_validate_json(path.read_text())
    print(review_checklist(art))
    print(f"\nContent hash: {content_hash(art)}")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    path = Path(args.artifact)
    art = CapabilityArtifact.model_validate_json(path.read_text())
    print(review_checklist(art))

    try:
        approved = approve(art, args.approver)
    except ApprovalPreconditionError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return 1
    try:
        write_artifact(path, approved)
    except ApprovalIntegrityError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return 1
    print(f"\n✓ Approved by {args.approver}")
    print(f"  Hash: {approved.approval.content_hash}")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    path = Path(args.artifact)
    art = CapabilityArtifact.model_validate_json(path.read_text())
    revoked = revoke(art)
    write_artifact(path, revoked)
    print(f"✓ Revoked: {path.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m control")
    sub = ap.add_subparsers(dest="command")

    p_review = sub.add_parser("review", help="Print review checklist")
    p_review.add_argument("artifact")

    p_approve = sub.add_parser("approve", help="Approve an artifact")
    p_approve.add_argument("artifact")
    p_approve.add_argument("--approver", required=True)

    p_revoke = sub.add_parser("revoke", help="Revoke approval")
    p_revoke.add_argument("artifact")

    args = ap.parse_args()
    if args.command == "review":
        return cmd_review(args)
    elif args.command == "approve":
        return cmd_approve(args)
    elif args.command == "revoke":
        return cmd_revoke(args)
    else:
        ap.print_help()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
