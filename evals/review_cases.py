"""P6: turn exported reviewer decisions into stub stress cases for a HUMAN to label.

A `dismiss` (the flag was wrong) or `correct` (the answer was wrong) is evidence that the stress set is missing a case.
This only drafts the stub: query, records and the expected action stay blank (`expected_action="unlabelled"`) until a
person writes them. Nothing here guesses a label from the system's own output (P1 "Don't")."""

from typing import Any, Dict, Iterable, List

from .stress import StressCase

UNLABELLED = "unlabelled"


def stub_cases(entries: Iterable[Dict[str, Any]]) -> List[StressCase]:
    """`entries`: `agent_activity_log` items. Only `kind == "review"` with decision dismiss/correct become stubs."""
    out, seen = [], set()
    for e in entries:
        if e.get("kind") != "review" or e.get("decision") not in ("dismiss", "correct"):
            continue
        cid = f"review-{e.get('run_id', 'run')}-{e.get('escalation_kind', 'x')}-{len(out)}"
        if (e.get("run_id"), e.get("claim_key"), e.get("escalation_kind")) in seen:
            continue
        seen.add((e.get("run_id"), e.get("claim_key"), e.get("escalation_kind")))
        out.append(
            StressCase(
                id=cid,
                kind="review",
                query="",
                expected_action=UNLABELLED,
                expected_claims={
                    "_flag": (
                        e.get("escalation_kind"),
                        e.get("claim_key"),
                        e["decision"],
                    )
                },
            )
        )
    return out
