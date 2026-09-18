# Configuration

`respro` ships with a bundled default configuration (`respro/config/defaults.toml`) that defines every scientific threshold, alignment parameter, allele-frequency bin, network timeout, and external URL the pipeline uses. You can override any of these values per-invocation with a user TOML file passed via the `--config` flag, without modifying the installed package.

## The `--config` flag

All three profiling/reporting commands accept `--config PATH` (`-c` for short):

```bash
respro fasta  --project my.db --fasta consensus.fasta --output out --config my_overrides.toml
respro vcf    --project my.db --vcf calls.vcf --ref-fasta ref.fasta --output out --config my_overrides.toml
respro regenerate --project my.db --results-db runs.db --run-id 1 --output out --config my_overrides.toml
```

The override file is a TOML document. Your values **overwrite** the bundled defaults for the keys you list; every key you omit keeps its bundled value. You only need to specify what you want to change.

Unknown sections, unknown keys, and type mismatches (e.g. a string where an integer is expected) are rejected — the command exits with an error naming the offending key and the allowed values, rather than silently producing a report built on unintended assumptions.

When an override is applied, `respro` logs an `INFO` message confirming the file was loaded (visible with `-v`).

## Overridable sections and keys

Every key below corresponds to an entry in `respro/config/defaults.toml`, where the bundled value and an inline rationale comment can be found.

### `[timeouts]` — network timeouts (seconds) and retry policy

| Key | Type | Default | Effect |
|---|---|---|---|
| `pubchem` | int | `7` | Per-request timeout for PubChem lookups. |
| `pubmed` | int | `7` | Per-request timeout for PubMed esummary. |
| `crossref` | int | `7` | Per-request timeout for Crossref. |
| `genbank_timeout` | int | `30` | `urlopen` timeout (seconds) for a single NCBI nuccore efetch attempt. |
| `genbank_max_retries` | int | `3` | Max efetch attempts before raising `RuntimeError`. |
| `genbank_backoff_base` | float | `1.0` | Exponential backoff base (seconds); doubled each retry (1, 2, 4). |

### `[urls]` — external service URL templates

These are URL templates containing `{placeholder}` segments. Override only if you need to point at a mirror or a local proxy.

| Key | Default |
|---|---|
| `pubchem_compound_page` | `https://pubchem.ncbi.nlm.nih.gov/compound/{cid}` |
| `pubchem_structure_png` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/PNG` |
| `pubchem_cid_lookup` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON` |
| `pubchem_description` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/description/JSON` |
| `pubchem_title` | `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/Title/JSON` |
| `ncbi_pubmed_esummary` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={pmid}&retmode=json` |
| `ncbi_pmc_idconv` | `https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={identifier}&format=json` |
| `crossref_works` | `https://api.crossref.org/works/{doi}` |
| `ncbi_protein_page` | `https://www.ncbi.nlm.nih.gov/protein/{protein_id}/` |
| `ncbi_nuccore_efetch` | `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=nuccore&id={accession}&rettype=gb&retmode=text` |
| `github_respro_db_raw` | `https://raw.githubusercontent.com/the-foxlab/respro-databases/main/databases` |

### `[parsing]` — publication-identifier parsing

| Key | Type | Default | Effect |
|---|---|---|---|
| `doi_prefixes` | list[str] | `['https://doi.org/', 'http://doi.org/']` | URL prefixes stripped/recognised when parsing DOI references. |

### `[matching]` — combination (formula) rule gating

| Key | Type | Default | Effect |
|---|---|---|---|
| `min_cooccurrence_combination_fraction` | float | `0.6666666666666666` | Formula (combination) rule gating: an AND clause fires only when the joint Fréchet lower bound's forced fraction (`lower / min(member frequencies)`) reaches this value. Decoupled from the codon policy (`[codon] min_cooccurrence_codon_fraction`). Also drives the `combination_fraction_pct` label shown in the report's frequency hover. |
| `frechet_epsilon` | float | `1e-9` | Numerical tolerance for Fréchet-bound acceptance on the formula path (member lower bound > eps, forced fraction ≥ min_fraction − eps). Independent of the codon-path `[codon] frechet_epsilon`. |

### `[codon]` — codon-level scientific thresholds

| Key | Type | Default | Effect |
|---|---|---|---|
| `min_cooccurrence_codon_fraction` | float | `0.6666666666666666` | Conservative Fréchet-intersection policy: a candidate codon state is emitted only when the minimum guaranteed co-occurrence fraction reaches this value. |
| `min_read_mapping_quality` | int | `20` | Minimum MAPQ for a read to count toward BAM-based same-codon SNP co-occurrence (VCF mode with `--bam` only). |
| `bam_base_quality_threshold` | int | `0` | Minimum base quality for a read to contribute to BAM per-position depth (`count_coverage`). `0` accepts all bases. |
| `frechet_epsilon` | float | `1e-9` | Numerical tolerance for Fréchet-bound acceptance (`lower > eps`). Loosen in noisy regimes; tightening below `1e-12` risks float instability. |

### `[similarity]` — amino-acid similarity classification

BLOSUM62 score thresholds. `score >= high` → `high`; `score >= moderate` → `moderate`; else `low`.

| Key | Type | Default |
|---|---|---|
| `high` | int | `1` |
| `moderate` | int | `0` |

### `[af_bins]` — allele-frequency bins (VCF mode)

Each value is `[lower_inclusive, upper_inclusive]`.

| Key | Type | Default |
|---|---|---|
| `high` | list[float] | `[0.75, 1.0]` |
| `intermediate` | list[float] | `[0.25, 0.7499]` |
| `low` | list[float] | `[0.01, 0.2499]` |

### `[af_bins_fasta]` — allele-frequency bins (FASTA mode)

FASTA frequencies are discrete (1.0, 0.5, 0.33, 0.25) due to IUPAC base expansion, so bin boundaries differ from VCF mode.

| Key | Type | Default |
|---|---|---|
| `high` | list[float] | `[0.75, 1.0]` |
| `intermediate` | list[float] | `[0.35, 0.74]` |
| `low` | list[float] | `[0.01, 0.34]` |

### `[alignment]` — minimap2/mappy alignment settings

These are passed to mappy for CDS-to-query mapping. The defaults are tuned for sensitivity across divergent sequences (~60–75% identity). Only change the scoring keys if you understand minimap2 scoring — wrong values can degrade alignment quality for divergent sequences.

| Key | Type | Default | Effect |
|---|---|---|---|
| `preset` | str | `'map-ont'` | Minimap2 preset. |
| `k` | int | `6` | Minimizer k-mer size. |
| `w` | int | `3` | Minimizer window size. |
| `best_n` | int | `1` | Keep best N alignments. |
| `gap_open_penalty` | int | `6` | Short-gap open penalty (O1 component of the scoring tuple). |
| `match_score` | int | `2` | Scoring tuple A component. |
| `mismatch_penalty` | int | `4` | Scoring tuple B component. |
| `gap_extension_penalty_1` | int | `2` | Scoring tuple E1 component. |
| `gap_open_penalty_2` | int | `24` | Scoring tuple O2 component. |
| `gap_extension_penalty_2` | int | `1` | Scoring tuple E2 component. |
| `intron_junction_tolerance` | int | `5` | Max CDS-position distance (nt) between a CIGAR `I` op and a known exon-junction offset for the `I` to be classified as an intron. Only applies to spliced (multi-segment) CDS. |

## Example override file

A short TOML listing only the keys you want to change — here raising the combination-rule co-occurrence fraction and tightening the high-AF bin label to 80% for a VCF run:

```toml
# my_overrides.toml
[matching]
min_cooccurrence_combination_fraction = 0.8

[af_bins]
high         = [0.8, 1.0]
intermediate = [0.25, 0.8]
low          = [0.0, 0.25]
```

```bash
respro vcf --project my.db --vcf calls.vcf --ref-fasta ref.fasta --output out --config my_overrides.toml
```

The report's AF-bin legend will read "high (≥80%)" instead of the default "high (≥75%)".

## Regenerate caveat

`respro regenerate` rebuilds a report from a **stored run** — it does not re-run alignment or annotation. Consequently only **report-stage** config keys affect regenerate:

- `[af_bins]` / `[af_bins_fasta]` — AF-bin labels
- `[matching]` — `combination_fraction_pct` label only; `[matching] frechet_epsilon` is a match-stage key with no effect on a regenerated report
- `[similarity]` — similarity classification thresholds

Overrides to `[alignment]`, `[codon]`, `[timeouts]`, `[urls]`, or `[parsing]` are **accepted** by the loader (so a single override file is portable across `fasta`/`vcf`/`regenerate`) but have **no effect** on a regenerated report, because those stages already ran when the run was first profiled and their results are persisted in the results database. To change alignment or codon behaviour, re-profile the sample with `respro fasta` / `respro vcf` and the desired `--config`.
