# Output Interpretation Guide (HTML, JSON, TSV)

ResistanceProfiler produces one required output and optional structured exports.

> [!TIP]
> Use the HTML report first for interpretation context, then use JSON/TSV for automation and downstream processing.

## HTML report (`*.report.html`)

Primary human-readable result artifact.

What to inspect first:

- sample and project identity
- total hits and matched rules
- per-feature mutation details and consequences
- phenotype and clinical phenotype context
- optional manual classifications

Best use:

- review by analysts and clinicians
- sharing a portable report artifact

## JSON export (`*.results.json`)

Structured machine-readable export for automation and reproducibility.

Top-level sections include:

- `run`
- `variant_result`
- `coverage_gap`
- `formula_rule_hit`
- `sample_classification`

Best use:

- downstream pipelines
- archival and deterministic regeneration
- data integration with external systems

> [!IMPORTANT]
> JSON exports are intended as reproducible artifacts and can be used directly with `respro regenerate --json`.

Regenerate a report from JSON:

```bash
respro regenerate \
  --project myrespro.db \
  --json my_output/sample_variants.results.json \
  --output my_output
```
