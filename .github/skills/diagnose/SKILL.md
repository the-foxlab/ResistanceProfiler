---
name: diagnose
description: 'Use when code misbehaves, tests fail, or a bug is unclear. Follow a reproduce -> minimize -> hypothesize -> instrument -> fix -> regression-test loop.'
argument-hint: 'Failing command, symptom, or subsystem that needs a disciplined debug pass.'
user-invocable: true
---

# Diagnose

## Overview

Use this skill when the code is failing and you need a disciplined debugging loop instead of a guess.

## Workflow

1. Reproduce the failure with the smallest reliable command or test.
2. Minimize the surface area until the failing path is obvious.
3. Form one concrete hypothesis about the root cause.
4. Instrument or inspect the narrow path that can confirm or disprove it.
5. Apply the smallest fix that addresses the cause, not just the symptom.
6. Run the narrow regression check first, then broaden only if needed.

## Debugging Rules

- Prefer the cheapest failing test or command that exposes the bug.
- Keep one active hypothesis at a time.
- Avoid broad search once the controlling code path is identified.
- If the failure is testable, prove the fix with the same check that failed.
- For this repository, prefer `python -m pytest` over the bare `pytest` entrypoint.

## Output Style

- State the observed symptom.
- State the current hypothesis.
- State the next check that would falsify it.
- State the fix only after the check has confirmed the cause.