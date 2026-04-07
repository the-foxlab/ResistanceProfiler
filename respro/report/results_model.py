"""
Structured results model — format-independent report data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from respro.db.models import AnnotatedVariant, ComboRuleHit, CoverageGap


@dataclass
class ProfilingResult:
    """
    Complete result of a single profiling run.
    From this object HTML exports are derived.
    """

    project_name: str = ''
    organism: str = ''
    reference_name: str = ''
    reference_length_nt: int = 0
    sample_name: str = ''
    vcf_name: str = ''
    run_timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec='seconds')
    )
    total_variants: int = 0
    variants_in_cds: int = 0
    resistance_hits: int = 0
    annotations: list[AnnotatedVariant] = field(default_factory=list)
    combo_hits: list[ComboRuleHit] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)

    @property
    def cds_annotations(self) -> list[AnnotatedVariant]:
        """
        Return annotations that fall within a coding region.

        :return: list of AnnotatedVariant with gene_name set
        """
        return [a for a in self.annotations if a.gene_name]

    def summary_dict(self) -> dict:
        """
        Return a JSON-serializable summary (no per-variant detail).

        :return: dict with project metadata and summary statistics
        """
        return {
            'project_name': self.project_name,
            'organism': self.organism,
            'reference': self.reference_name,
            'reference_length_nt': self.reference_length_nt,
            'sample': self.sample_name,
            'vcf': self.vcf_name,
            'timestamp': self.run_timestamp,
            'total_variants': self.total_variants,
            'variants_in_cds': self.variants_in_cds,
            'resistance_hits': self.resistance_hits,
            'combo_rule_hits': len(self.combo_hits),
        }