---
name: zoom-out
description: 'Use when you need broader architectural context before editing code. Summarize the subsystem, its boundaries, dependencies, and the risks of changing it.'
argument-hint: 'Subsystem, module boundary, or feature area that needs higher-level context.'
user-invocable: true
---

# Zoom Out

## Overview

Use this skill when a local change is easier to make after understanding the larger flow around it.

## Workflow

1. Identify the entry points that own the behavior.
2. Trace the data flow through the surrounding modules.
3. Note which boundaries must stay stable and which ones can change.
4. Call out the smallest safe edit surface.
5. Highlight any tests, docs, or agents that would drift if the boundary changes.

## What To Report

- The subsystem's job in the project
- Upstream inputs and downstream consumers
- Shared helpers or modules that look tempting but should stay independent
- Edge cases and invariants that matter for the change
- Any architectural docs that should be updated if the boundary moves

## Output Style

- Keep it higher-level than a code review.
- Use it to inform the next implementation step, not to replace it.