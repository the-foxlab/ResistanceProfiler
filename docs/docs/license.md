---
title: License
description: Licensing information for ResistanceProfiler
---

# License

## Source code

ResistanceProfiler source code is released under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0).

## External data

External references, rules, and publication-linked datasets may have separate licenses or citation requirements. Users are responsible for compliant use of third-party data in their own environments.

## Data usage

- Uploads and generated reports are temporary. The web app deletes files older than the configured result TTL (default 24 hours) via a background sweep.
- The CLI stores results only where you tell it to (via `--output` and `--results-db`). No data leaves your machine unless you use the `--additional-info` lookups.
- Avoid naming results with sensitive information such as patient identifiers or names.
