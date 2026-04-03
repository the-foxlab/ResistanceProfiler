"""
Structured results model — format-independent report data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from respro.db.models import AnnotatedVariant, ComboRuleHit


@dataclass
class ProfilingResult:
    """
    Complete result of a single profiling run.

    This object is the single source of truth from which HTML, JSON, TSV,
    and plot exports are derived.
    """

    project_name: str = ''
    organism: str = ''
    reference_name: str = ''
    reference_length_nt: int = 0
    sample_name: str = ''
    vcf_path: str = ''
    run_timestamp: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec='seconds')
    )
    total_variants: int = 0
    variants_in_cds: int = 0
    resistance_hits: int = 0
    annotations: list[AnnotatedVariant] = field(default_factory=list)
    combo_hits: list[ComboRuleHit] = field(default_factory=list)

    @property
    def cds_annotations(self) -> list[AnnotatedVariant]:
        """
        Return annotations that fall within a coding region.

        :return: list of AnnotatedVariant with gene_name set
        """
        return [a for a in self.annotations if a.gene_name]

    @property
    def hit_annotations(self) -> list[AnnotatedVariant]:
        """
        Return annotations with at least one resistance rule match.

        :return: list of AnnotatedVariant with rule matches
        """
        return [a for a in self.annotations if a.is_resistance_hit]

    @property
    def missense_annotations(self) -> list[AnnotatedVariant]:
        """
        Return annotations with missense consequence.

        :return: list of missense AnnotatedVariant
        """
        return [a for a in self.annotations if a.consequence == 'missense']

    @property
    def synonymous_annotations(self) -> list[AnnotatedVariant]:
        """
        Return annotations with synonymous consequence.

        :return: list of synonymous AnnotatedVariant
        """
        return [a for a in self.annotations if a.consequence == 'synonymous']

    # ----- serialisation -----

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
            'vcf': self.vcf_path,
            'timestamp': self.run_timestamp,
            'total_variants': self.total_variants,
            'variants_in_cds': self.variants_in_cds,
            'resistance_hits': self.resistance_hits,
            'combo_rule_hits': len(self.combo_hits),
        }

    def variants_as_dicts(self) -> list[dict]:
        """
        Return all annotations as a list of flat dicts.

        :return: list of dicts with variant and annotation details
        """
        rows = []
        for ann in self.annotations:
            rows.append({
                'chrom': ann.variant.chrom,
                'pos': ann.variant.pos + 1,
                'ref': ann.variant.ref,
                'alt': ann.variant.alt,
                'allele_freq': ann.variant.allele_freq,
                'depth': ann.variant.depth,
                'gene': ann.gene_name,
                'codon_pos': ann.codon_pos + 1,  # 0-based internally, 1-based in output
                'ref_codon': ann.ref_codon,
                'alt_codon': ann.alt_codon,
                'ref_aa': ann.ref_aa,
                'alt_aa': ann.alt_aa,
                'consequence': ann.consequence,
                'is_combined_codon_event': ann.is_combined_codon_event,
                'combined_member_count': ann.combined_member_count,
                'af_bin': ann.af_bin,
                'resistance_hit': ann.is_resistance_hit,
                'drug_hits': ann.drug_hits_json(),
            })
        return rows

    def to_json(self, indent: int = 2) -> str:
        """Full JSON serialisation."""
        data = self.summary_dict()
        data['variants'] = self.variants_as_dicts()
        data['combo_rule_hits'] = [hit.to_dict() for hit in self.combo_hits]
        return json.dumps(data, indent=indent, default=str)

