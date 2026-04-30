# Tool Comparison for Antiviral Resistance Analysis

ResistanceProfiler (ResPro) differs from many established antiviral resistance tools by acting as a pathogen-agnostic framework with both CLI and web workflows, support for FASTA and VCF-based analysis, reusable project databases, and report regeneration from stored results. Most other tools in this space are strong but narrower (often single-pathogen and web-only) and typically do not combine custom rule ingestion, cross-pathogen support, and reproducible run storage in one system. 

## Feature Matrix (Compared to ResPro)

| Tool | Primary scope | Open source | Web interface | Local CLI/offline use | Input support (consensus FASTA) | Input support (VCF) | Input support (NGS frequency/deep sequencing) | Multi-pathogen in one framework | Custom/curated rule import by user | Persistent run storage + regenerate reports |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **ResistanceProfiler (ResPro)** | Pathogen-agnostic antiviral profiling framework | Yes (MIT) | Yes | Yes | Yes | Yes | Partial (VCF; BAM-assisted coverage; no direct FASTQ ingest in core CLI) | Yes | Yes (TSV + optional formula rules) | Yes |
| Stanford HIVdb Sierra | HIV-1 resistance interpretation | Yes | Yes | Yes (API/local deployments) | Yes | No | Yes (CodFreq workflow) | No | No | No |
| Stanford CoV-RDB Sierra | SARS-CoV-2 antiviral/mAb resistance interpretation | Yes | Yes | Partial | Yes | No | Yes (CodFreq workflow) | No | No | No |
| HCV-GLUE | HCV genotyping + DAA resistance | Yes | Yes | Yes | Yes | No | Partial (SAM/BAM-supported analysis) | No | No | No |
| geno2pheno[hcv] | HCV resistance interpretation (NS3/NS5A/NS5B) | No | Yes | No | Yes | No | No | No | No | No |
| HIV-GRADE | HIV/HBV rule-system comparison service | No | Yes | No | Yes (or mutation lists) | No | No | No | No | No |
| CHARMD/HSA | Herpesvirus mutation annotation and resistance context | No (public access service) | Yes | No | Yes | No | No | No | No | No |
| HerpesDRG (database) | Curated herpes resistance rule database | Yes (database project) | N/A | N/A (dataset, not a profiling engine) | N/A | N/A | N/A | No | N/A | N/A |

## Notes

- ResPro can consume maintained, curated databases and is designed to support compatibility workflows for resources such as HerpesDRG.
- For tools marked as "Partial" in offline/deep-sequencing columns, capabilities usually depend on companion pipelines or specific input pre-processing formats rather than direct end-to-end FASTQ to report execution in a single command.
