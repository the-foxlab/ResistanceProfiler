# Troubleshooting and FAQ

## Common issues

### The backend does not start on port 8000

Symptom:

- `address already in use`

Fix:

- Start with another port, for example:

```bash
RESPRO_WEB_PORT=8011 python -m web.backend.main
```

### VCF profiling fails with contig mismatch

Symptom:

- error indicating VCF contig names do not match the reference FASTA

Fix:

- Use a reference FASTA derived from the same coordinate space and naming as the VCF.

> [!CAUTION]
> A VCF and reference FASTA that look biologically similar but use different coordinate naming conventions can still fail mapping.

### I only need to validate rules before import

Use dry-run validation:

```bash
respro add --project data/demo-zeta/project/project.db --rules data/demo-zeta/inputs/rules_hsv1.tsv --validate
```

### My report is lost after reruns

Use a results database and regenerate from stored run:

```bash
respro manage results data/demo-zeta/results/results.db --list
respro regenerate --project data/demo-zeta/project/project.db --results-db data/demo-zeta/results/results.db --run-id 1 --output data/demo-zeta/output
```

## FAQ

### Is HTML always produced?

Yes. HTML report output is always generated in profiling and regeneration flows.

### Can I export machine-readable output?

Yes. Use `--export json` and/or `--export tabular` where supported.

### Do I need internet access for initialization?

No. Use `--no-additional-info` to skip optional metadata lookups.
