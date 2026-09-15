"""State-graph pruning with a write barrier.

Removes detours from a discovery transcript — cycles where the agent visited a
state, went elsewhere, came back, and continued. The shortest path through
visited states is kept.

The write barrier: pruning is legal only across ``safe`` actions. Any
``guarded_write`` or ``irreversible`` in a segment forces the walk kept
linearly, because screen state is not server state. Anything not provably a
loop is flagged, not deleted.
"""

from __future__ import annotations

from agent.loop import StepRecord


def prune(records: list[StepRecord]) -> list[StepRecord]:
    """Remove detours from the transcript, respecting the write barrier.

    A detour is a sequence of steps where the observation hash returns to a
    previously-visited state. If every action in the detour is ``safe``,
    the detour is removed. Otherwise it's kept — the write may have changed
    server state even if screen state looks the same.

    Terminal actions (done, escalate, give_up) are always kept.
    """
    if len(records) <= 1:
        return list(records)

    # Filter to action steps only (exclude done/escalate/give_up for graph walk)
    action_steps = [r for r in records if r.action_kind not in ("done", "escalate", "give_up")]
    terminal = [r for r in records if r.action_kind in ("done", "escalate", "give_up")]

    if not action_steps:
        return list(records)

    # Walk the hash sequence looking for revisits
    pruned = _prune_pass(action_steps)

    # Re-attach terminal steps
    return pruned + terminal


def _prune_pass(steps: list[StepRecord]) -> list[StepRecord]:
    """One pruning pass over action steps."""
    # Build a list of (step, obs_hash) pairs. A detour is when we see a hash
    # we've seen before — the segment between the first occurrence and the
    # current one is the detour.
    result: list[StepRecord] = []
    hash_to_idx: dict[str, int] = {}

    for step in steps:
        h = step.obs_hash
        if h in hash_to_idx:
            prev_idx = hash_to_idx[h]
            # The segment between the two visits: steps after the first
            # occurrence up to (not including) the current step.
            segment = result[prev_idx + 1:]

            # A segment with zero steps means two consecutive steps share
            # the same hash — e.g. clicking loaded an iframe but the main
            # frame hash didn't change.  Not a detour.
            if len(segment) == 0:
                result.append(step)
                hash_to_idx[h] = len(result) - 1
                continue

            # Write barrier: if any step in the detour has non-safe risk,
            # keep the segment — screen state may be the same but server
            # state is not.
            has_write = any(
                s.verdict_risk in ("guarded_write", "irreversible")
                for s in segment
            )

            if has_write:
                result.append(step)
                hash_to_idx[h] = len(result) - 1
            else:
                # Prune: cut back to prev_idx (keep the first visit) and
                # replace with the current step (same hash, fresh start).
                result = result[:prev_idx]
                result.append(step)
                # Rebuild hash index for remaining steps
                hash_to_idx = {}
                for i, s in enumerate(result):
                    hash_to_idx[s.obs_hash] = i
        else:
            result.append(step)
            hash_to_idx[h] = len(result) - 1

    return result
