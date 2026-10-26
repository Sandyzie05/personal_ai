---
name: Conflict Escalation
description: When to stop and ask the user instead of answering
---

Stop and ask the user when any of these holds, and say which one:
1. Conflict: two sources disagree on the same fact (amount, date, direction).
2. Unavailable or stale: a needed provider failed or its data is older than the freshness limit.
3. Failed or skipped step: a delegated step did not complete.
4. Untrusted write: a proposed memory change or action depends on untrusted-derived data.
5. Low confidence on a high-impact claim.
Escalation message: what is unresolved, the options with their evidence, and what you recommend checking. Never resolve by guessing.
