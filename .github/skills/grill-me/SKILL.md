---
name: grill-me
description: 'Use when a task is underspecified, risky, or needs alignment before code changes. Ask a short set of targeted questions to resolve the blocking ambiguity.'
argument-hint: 'Task goal, constraints, scope, and what must be true before implementation.'
user-invocable: true
---

# Grill Me

## Overview

Use this skill before substantial work when the request is still fuzzy. The goal is to remove the minimum amount of ambiguity needed to avoid rework.

## Workflow

1. Restate the current understanding in one short paragraph.
2. Ask only the questions that change the implementation path, test strategy, or acceptance criteria.
3. Prefer concrete options over open-ended prompts when the choice is likely to be binary.
4. Stop as soon as the blocking uncertainty is resolved.

## Question Focus

- User goal and definition of done
- Scope boundaries and out-of-scope items
- Required inputs, outputs, and runtime constraints
- Validation expectations and acceptable trade-offs
- Any repo-specific conventions that should be preserved

## Output Style

- Keep the interview short.
- Do not ask about details that are already clear from the repo context.
- If the task is already clear enough, skip the questions and move straight to the implementation target.