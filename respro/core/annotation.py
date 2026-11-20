"""Codon-aware variant annotation orchestrator — dispatches single events and co-codon SNP groups."""

from __future__ import annotations

import logging

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.codons import (
    CONSEQUENCE_LABELS,
    HIGH_IMPACT_CONSEQUENCES,
    annotate_variant_in_feature,
    assign_af_bins,
    classify_similarity,
    classify_snp_consequence,
    normalize_mutation,
    reverse_complement,
    translate_codon,
)
from respro.core.combined_snp import (
    BamCooccurrence,
    annotate_combined_snp_codon,
    plan_combined_snp_groups,
)
from respro.db.models import AnnotatedVariant, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

# Preserve test-imported helper names cleanly without circular imports.
_annotate_variant_in_feature = annotate_variant_in_feature
_classify_snp_consequence = classify_snp_consequence

__all__ = [
    'CONSEQUENCE_LABELS',
    'HIGH_IMPACT_CONSEQUENCES',
    'annotate_variants',
    'assign_af_bins',
    'classify_similarity',
    'normalize_mutation',
    'reverse_complement',
    'translate_codon',
]


def annotate_variants(
    variants: list[VariantCall],
    features: list[FeatureRecord],
    is_fasta_mode: bool = False,
    bam_cooccurrence: BamCooccurrence | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> list[AnnotatedVariant]:
    """Annotate variants with codon-aware AA consequences, resolving co-codon SNP groups together."""
    results: list[AnnotatedVariant] = []
    skipped_non_snp = 0
    group_plan = plan_combined_snp_groups(variants, features)

    for var_idx, var in enumerate(variants):
        matching_features = [f for f in features if f.contains(var.pos)]
        if not matching_features:
            results.append(AnnotatedVariant(variant=var, is_fasta_mode=is_fasta_mode))
            continue

        for feature in matching_features:
            codon_idx = feature.codon_index(var.pos)
            group = group_plan.get((feature.id, codon_idx)) if codon_idx is not None else None
            if group is not None:
                if var_idx == group[0]:
                    members = [variants[i] for i in group]
                    for ann in annotate_combined_snp_codon(
                        members, feature, bam_cooccurrence=bam_cooccurrence, cfg=cfg,
                    ):
                        ann.is_fasta_mode = is_fasta_mode
                        results.append(ann)
                continue

            anns = annotate_variant_in_feature(var, feature)
            if not anns:
                skipped_non_snp += 1
                continue
            for ann in anns:
                ann.is_fasta_mode = is_fasta_mode
                results.append(ann)

    logger.info(
        'Annotated %d variant(s) -> %d annotation(s) (%d in CDS, %d non-assessable skipped)',
        len(variants),
        len(results),
        sum(1 for a in results if a.feature_name),
        skipped_non_snp,
    )
    return results
