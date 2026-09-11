"""
Codon-aware variant annotation — amino acid consequence classification, mutation normalisation,
and amino acid similarity scoring.
"""

from __future__ import annotations

import logging
import re

from Bio.Align.substitution_matrices import load as _load_matrix
from Bio.Seq import Seq

from respro.config.cli_settings import CLI_CONFIG
from respro.db.models import AnnotatedVariant, CodonState, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

_BLOSUM62 = _load_matrix('BLOSUM62')

# Numerical tolerance for Fréchet-bound acceptance (lower > eps). Small enough
# that mathematically exact boundary cases (lower == 0) are stable.
_FRECHET_EPS = 1e-9

# Shared high-impact consequence set and human-readable labels.
HIGH_IMPACT_CONSEQUENCES: frozenset[str] = frozenset({
    'frameshift', 'stop_gained', 'stop_lost', 'start_lost', 'insertion', 'deletion',
})
CONSEQUENCE_LABELS: dict[str, str] = {
    'frameshift': 'frameshift',
    'stop_gained': 'premature stop',
    'stop_lost': 'stop loss',
    'start_lost': 'start loss',
    'insertion': 'in-frame insertion',
    'deletion': 'in-frame deletion',
}

# Compiled patterns for normalize_mutation — module-level per Python performance convention.
_RE_FS_ANY = re.compile(r'^(?:[A-Z*]\d+)?(?:fs|frameshift)', re.IGNORECASE)
_RE_STOP_FULL = re.compile(r'^[A-Z*]\d+stop$', re.IGNORECASE)
_RE_REWRITE = re.compile(r'^([A-Z*]+)(\d+)([A-Z*]+)$', re.IGNORECASE)
_RE_HGVS_INS = re.compile(r'^([A-Z*])(\d+)(?:_[A-Z*]\d+)?ins([A-Z*]+)$', re.IGNORECASE)
_RE_HGVS_DEL = re.compile(r'^([A-Z*])(\d+)(?:_[A-Z*]\d+)?del([A-Z*]+)?$', re.IGNORECASE)
_RE_DEL_PREFIX = re.compile(r'^del([A-Z*]+)?(?:[A-Z*]\d+|\d+)$', re.IGNORECASE)
_RE_BARE_AA = re.compile(r'^[A-Z]$', re.IGNORECASE)


def annotate_variants(
    variants: list[VariantCall],
    features: list[FeatureRecord],
    is_fasta_mode: bool = False,
) -> list[AnnotatedVariant]:
    """
    Annotate a list of variants with codon-aware amino acid consequences.

    Handles SNPs, in-frame insertions, in-frame deletions, frameshifts, and in-frame complex
    indels in CDS regions. Only variants outside any CDS are skipped (included with empty feature_name).

    SNP consequences can use a query codon from FASTA-based remapping when
    available (``VariantCall.query_ref_codon``).

    Co-codon SNPs (two or more SNPs at distinct positions of the same feature
    codon) are evaluated as combined codon events via Fréchet bounds; each
    member SNP is emitted as its own per-SNP annotation carrying the accepted
    combined states.

    :param variants: parsed variant calls (0-based positions). Variants with no CDS hit are
        included with empty feature_name.
    :param features: feature annotations for the reference
    :param is_fasta_mode: mark emitted annotations as FASTA-derived
    :return: list of AnnotatedVariant
    """
    results: list[AnnotatedVariant] = []
    skipped_non_snp = 0
    group_plan = _plan_combined_snp_groups(variants, features)

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
                    combined_annotations = _annotate_combined_snp_codon(members, feature)
                    for ann in combined_annotations:
                        ann.is_fasta_mode = is_fasta_mode
                        results.append(ann)
                continue
            anns = _annotate_variant_in_feature(var, feature)
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


def _suppress_ruleless_overlap_annotations(
    annotations: list[AnnotatedVariant],
    rule_feature_names: set[str],
    features: list[FeatureRecord] | None = None,
    scope_chroms: set[str] | None = None,
) -> list[AnnotatedVariant]:
    """
    Suppress annotations for ruleless features that overlap a ruled feature.

    Two suppression mechanisms run, in order:

    1. Feature-overlap suppression (when ``features`` is provided): a ruleless
       feature whose genomic span overlaps any ruled feature on the same reference
       is suppressed entirely — every annotation carrying that feature name is
       dropped. This catches the common case where a ruleless feature extends
       beyond a ruled feature (e.g. UL24 overlapping UL23) and carries variants
       at loci no ruled-feature variant shares. Overlap is evaluated per
       reference: features are grouped by ``reference_id`` and a ruleless feature
       only competes with ruled features on the same reference. Scoping by
       ``reference_id`` is correct because internal references share the same
       coordinate origin; a ruled feature on refA cannot "claim" coordinates that
       belong to a ruleless feature on refB.

       The annotation filter is scoped by ``scope_chroms`` when provided: only
       annotations whose ``variant.chrom`` is in ``scope_chroms`` are dropped.
       This is required for the VCF multi-reference path, which calls this
       function once per reference with only that reference's features. Two
       references may share a ruleless feature name (e.g. HSV-1 and HCMV both
       carry UL24); without chrom scoping, suppressing the name on refA would
       also drop refB's standalone UL24 annotations — a silent cross-reference
       over-suppression. When ``scope_chroms`` is None (FASTA single-reference
       path), the filter applies by name across all annotations, since a single
       reference cannot have cross-reference collisions.
    2. Locus-group suppression (always): groups annotations by variant locus
       ``(chrom, pos, ref, alt)`` and, for groups with more than one annotation,
       keeps only the ruled-feature annotations when at least one is ruled. This
       catches the in-overlap-zone case where a single variant lands inside both
       a ruled and a ruleless feature (the original overlap-bug scenario).

    :param annotations: list of annotated variants
    :param rule_feature_names: feature names covered by at least one rule
    :param features: feature records for the reference(s) the annotations belong
        to; when provided, enables feature-overlap suppression. When None, only
        locus-group suppression runs (legacy behaviour).
    :param scope_chroms: when provided, feature-overlap suppression only drops
        annotations whose ``variant.chrom`` is in this set. Pass the current
        reference's chroms in the VCF multi-reference path; pass None for the
        FASTA single-reference path.
    :return: filtered annotation list
    """
    # 1. Feature-overlap suppression: drop ruleless features that overlap a ruled
    #    feature on the same reference. Scope the drop to ``scope_chroms`` when
    #    provided so a suppressed name on one reference does not erase the same
    #    name's annotations on a different reference.
    if features:
        suppressed_feature_names = _ruleless_features_overlapping_ruled(features, rule_feature_names)
        if suppressed_feature_names:
            if scope_chroms is not None:
                annotations = [
                    ann for ann in annotations
                    if not (
                        ann.feature_name in suppressed_feature_names
                        and ann.variant.chrom in scope_chroms
                    )
                ]
            else:
                annotations = [
                    ann for ann in annotations if ann.feature_name not in suppressed_feature_names
                ]

    # 2. Locus-group suppression: for variants landing inside both a ruled and a
    #    ruleless feature at the same locus, keep only the ruled annotations.
    variant_groups: dict[tuple[str, int, str, str], list[AnnotatedVariant]] = {}
    for ann in annotations:
        variant_key = (ann.variant.chrom, ann.variant.pos, ann.variant.ref, ann.variant.alt)
        if variant_key not in variant_groups:
            variant_groups[variant_key] = []
        variant_groups[variant_key].append(ann)

    filtered: list[AnnotatedVariant] = []
    for group in variant_groups.values():
        if len(group) == 1:
            filtered.extend(group)
            continue

        has_ruled_feature = any(ann.feature_name in rule_feature_names for ann in group)
        if has_ruled_feature:
            filtered.extend(ann for ann in group if ann.feature_name in rule_feature_names)
        else:
            filtered.extend(group)

    return filtered


def _ruleless_features_overlapping_ruled(
    features: list[FeatureRecord],
    rule_feature_names: set[str],
) -> set[str]:
    """
    Return the names of ruleless features that overlap any ruled feature.

    Overlap is evaluated per reference (``reference_id``): a ruleless feature only
    competes with ruled features on the same reference, because internal references
    share the same coordinate origin and a span on refA is unrelated to the same
    numeric span on refB. Two half-open intervals ``[start, end)`` overlap when
    ``start < other_end and other_start < end``.

    :param features: feature records (may span multiple references)
    :param rule_feature_names: feature names covered by at least one rule
    :return: set of ruleless feature names that overlap a ruled feature
    """
    ruled_by_ref: dict[int, list[FeatureRecord]] = {}
    ruleless_by_ref: dict[int, list[FeatureRecord]] = {}
    for feat in features:
        if feat.name in rule_feature_names:
            ruled_by_ref.setdefault(feat.reference_id, []).append(feat)
        else:
            ruleless_by_ref.setdefault(feat.reference_id, []).append(feat)

    suppressed: set[str] = set()
    for ref_id, ruleless_features in ruleless_by_ref.items():
        ruled_features = ruled_by_ref.get(ref_id)
        if not ruled_features:
            continue
        for ruleless in ruleless_features:
            for ruled in ruled_features:
                if ruleless.start < ruled.end and ruled.start < ruleless.end:
                    suppressed.add(ruleless.name)
                    break
    return suppressed


def reverse_complement(seq: str) -> str:
    """
    Return the reverse complement of a DNA sequence.

    :param seq: DNA sequence
    :return: reverse complement of the sequence
    """
    return str(Seq(seq.upper()).reverse_complement())


def translate_codon(codon: str) -> str:
    """
    Translate a three-letter DNA codon to a single-letter amino acid.

    Returns '*' for stop codons, '?' for ambiguous or invalid codons.

    :param codon: three-letter DNA codon
    :return: single-letter amino acid code
    """
    codon = codon.upper()
    if len(codon) != 3:
        return '?'
    try:
        aa = str(Seq(codon).translate())
    except Exception as exc:
        logger.debug('Codon translation failed for %r: %s', codon, exc)
        return '?'
    return aa if aa else '?'


def _is_snp(ref: str, alt: str) -> bool:
    """Return True when both REF and ALT are single nucleotides."""
    return len(ref) == 1 and len(alt) == 1


def _is_insertion(ref: str, alt: str) -> bool:
    """Return True when ALT is longer than REF (VCF anchor-base convention)."""
    return len(alt) > len(ref)


def _is_deletion(ref: str, alt: str) -> bool:
    """Return True when REF is longer than ALT (VCF anchor-base convention)."""
    return len(ref) > len(alt)


def _translate_indel_bases(bases: str, strand: str) -> str:
    """
    Translate inserted or deleted nucleotide bases to amino acids.

    For negative-strand features the bases (given in genomic / VCF orientation) are
    reverse-complemented before translation so they are in coding orientation.

    :param bases: nucleotide string in VCF (genomic) orientation; must be a multiple of 3
    :param strand: '+' or '-'
    :return: amino acid string
    """
    oriented = reverse_complement(bases) if strand == '-' else bases.upper()
    return str(Seq(oriented).translate())


def _compute_codon_frechet_states(
    variants_at_codon: list[dict],
    ref_codon: str,
    min_fraction: float,
    eps: float = _FRECHET_EPS,
) -> list[CodonState]:
    """
    Compute the sharp Fréchet intersection bounds for every candidate codon state.

    For a codon with ``k`` distinct variant-bearing nucleotide positions (k = 2 or 3),
    enumerate every possible exact codon state (cartesian product of {ref, ALT} per
    position). For each state compute the Fréchet bounds:

    - ``q_i = f_i`` if the candidate carries variant ``i``, else ``q_i = 1 - f_i``.
    - ``lower = max(0.0, sum(q) - (k - 1))`` — minimum guaranteed co-occurrence.
    - ``upper = min(q)`` — maximum possible co-occurrence.
    - ``forced_fraction = lower / upper`` if ``upper > 0`` else ``0.0`` — the minimum
      fraction of the rarest required condition forced into this codon state.

    A state is ``accepted`` when ``lower > eps`` and ``forced_fraction >= min_fraction``.

    This is a conservative codon-level inference based on the minimum guaranteed
    intersection of the corresponding viral subpopulations. It does NOT infer
    physical phase and is NOT a probability that variants are linked. The
    ``min_fraction`` threshold is an explicit conservative interpretation policy:
    passing means the guaranteed portion is at least twice the potentially unshared
    portion (at 2/3).

    Multiallelic same-position inputs (two ALTs at the same codon position, e.g.
    IUPAC-reference or VCF multiallelic sites) cannot be combined into valid codon
    states and yield an empty list — the caller falls back to single-SNP annotation.

    :param variants_at_codon: list of ``{'codon_pos': int, 'alt': str, 'freq': float}``
        dicts, one per variant-bearing position. ``codon_pos`` is 0-based within the
        codon; ``alt`` is the ALT base in coding orientation; ``freq`` is the marginal
        allele frequency.
    :param ref_codon: the internal reference codon (3 bases, coding orientation).
    :param min_fraction: acceptance threshold for ``forced_fraction`` (e.g. 2/3),
        loaded from ``defaults.toml`` ([codon] section).
    :param eps: numerical tolerance; states with ``lower <= eps`` are rejected.
    :return: list of :class:`CodonState` for every enumerated candidate state
        (accepted or not), or an empty list when the input is not combinable
        (single variant, multiallelic same-position, or positions out of range).
    """
    if len(ref_codon) != 3:
        return []

    # Validate positions and reject multiallelic same-position inputs.
    positions: list[int] = []
    seen_positions: set[int] = set()
    for v in variants_at_codon:
        cp = v['codon_pos']
        if not isinstance(cp, int) or cp < 0 or cp >= 3:
            return []
        if cp in seen_positions:
            return []
        seen_positions.add(cp)
        positions.append(cp)

    k = len(positions)
    if k < 2:
        return []

    ref_bases = list(ref_codon.upper())

    # Enumerate the cartesian product of {ref, ALT} per variant-bearing position.
    # Each choice is a tuple of (variant_index, carries_variant) per position.
    accepted_states: list[CodonState] = []
    for mask in range(1 << k):
        # Build the candidate codon and the per-position q values.
        codon_bases = list(ref_bases)
        q_values: list[float] = []
        member_indices: list[int] = []
        for i, cp in enumerate(positions):
            carries = bool(mask & (1 << i))
            alt_base = variants_at_codon[i]['alt'].upper()
            freq = float(variants_at_codon[i]['freq'])
            if carries:
                codon_bases[cp] = alt_base
                q_values.append(freq)
                member_indices.append(i)
            else:
                q_values.append(1.0 - freq)

        alt_codon = ''.join(codon_bases)
        lower = max(0.0, sum(q_values) - (k - 1))
        upper = min(q_values)
        forced_fraction = lower / upper if upper > 0 else 0.0
        accepted = lower > eps and forced_fraction >= min_fraction - eps
        alt_aa = translate_codon(alt_codon)

        accepted_states.append(CodonState(
            alt_codon=alt_codon,
            alt_aa=alt_aa,
            lower=lower,
            upper=upper,
            forced_fraction=forced_fraction,
            accepted=accepted,
            member_indices=tuple(member_indices),
        ))

    return accepted_states


def _plan_combined_snp_groups(
    variants: list[VariantCall],
    features: list[FeatureRecord],
) -> dict[tuple[int, int], list[int]]:
    """
    Return codon groups that should be evaluated as combined SNP events.

    A codon with two or more SNPs at distinct codon positions is a candidate.
    The Fréchet test in :func:`_annotate_combined_snp_codon` decides whether any
    combined state is actually emitted; grouping here only collects co-codon SNPs.

    :param variants: input variant list
    :param features: feature records
    :return: {(feature_id, codon_idx): [variant_index, ...]}
    """
    grouped: dict[tuple[int, int], list[int]] = {}
    for idx, var in enumerate(variants):
        if not _is_snp(var.ref, var.alt):
            continue
        for feature in features:
            if not feature.contains(var.pos):
                continue
            codon_idx = feature.codon_index(var.pos)
            if codon_idx is None or codon_idx < 0:
                continue
            key = (feature.id, codon_idx)
            grouped.setdefault(key, []).append(idx)

    planned: dict[tuple[int, int], list[int]] = {}
    for key, member_indices in grouped.items():
        if len(member_indices) < 2:
            continue
        planned[key] = sorted(member_indices)
    return planned


def _annotate_combined_snp_codon(
    variants: list[VariantCall],
    feature: FeatureRecord,
) -> list[AnnotatedVariant]:
    """
    Annotate multiple SNPs in one codon as per-SNP combined-state events.

    For each member SNP, emit one :class:`AnnotatedVariant` carrying its own
    ``allele_freq``/``ref``/``alt``, a single-exchange ``alt_codon``/``alt_aa``
    (codon with only that SNP applied), and a ``combined_states`` list of the
    Fréchet-accepted codon states that include this member.

    Forced-overlap exception: when a member's single-exchange state is
    Fréchet-rejected (``lower <= eps``) and there is an accepted all-carried
    state with ``forced_fraction == 1.0`` (the Fréchet overlap is 100 %, i.e.
    a co-member is at frequency 1.0), the member's single ``alt_codon``/``alt_aa``
    is replaced by that forced all-carried state. The promoted state is dropped
    from ``combined_states`` to avoid showing the same effect twice. Own-solo
    accepted states stay in ``combined_states`` even when they equal the single.

    Falls back to plain single-SNP annotation (no ``combined_states``) when the
    Fréchet helper returns no states (multiallelic same-position, or no accepted
    state for any member).

    :param variants: SNPs from the same codon (same feature)
    :param feature: feature containing the codon
    :return: one annotation per member SNP (combined), or per-member single-SNP
        annotations on fallback
    """
    if not variants:
        raise ValueError('Combined SNP annotation requires at least one variant')

    seq_cds = feature.nt_sequence.upper()
    anchor = sorted(variants, key=lambda v: v.pos)[0]
    codon_idx = feature.codon_index(anchor.pos)
    if codon_idx is None:
        raise ValueError(
            f'Combined SNP annotation requires coding codon index for feature {feature.name!r} '
            f'at genomic position {anchor.pos}'
        )
    codon_start = feature.codon_start + (codon_idx * 3)
    internal_codon = seq_cds[codon_start:codon_start + 3]
    ref_aa = translate_codon(internal_codon)

    # Resolve each member's coding-orientation ALT base and codon position.
    members: list[tuple[VariantCall, int, str]] = []
    seen_positions: dict[int, str] = {}
    for var in sorted(variants, key=lambda v: v.pos):
        codon_pos = feature.codon_position_in_codon(var.pos)
        if codon_pos is None:
            raise ValueError(
                f'Combined SNP annotation requires coding codon position for feature {feature.name!r} '
                f'at genomic position {var.pos}'
            )
        alt_base = reverse_complement(var.alt) if feature.strand == '-' else var.alt.upper()
        if codon_pos in seen_positions and seen_positions[codon_pos] != alt_base:
            # Multiallelic same-position: cannot form valid combined codon states.
            # Fall back to per-member single-SNP annotation.
            return _combined_fallback_single_snp(variants, feature)
        seen_positions[codon_pos] = alt_base
        members.append((var, codon_pos, alt_base))

    # Build the Fréchet input specs (one per distinct variant-bearing position).
    frechet_specs = [
        {'codon_pos': cp, 'alt': ab, 'freq': float(v.allele_freq)}
        for v, cp, ab in members
    ]
    min_fraction = CLI_CONFIG.codon.min_cooccurrence_codon_fraction
    states = _compute_codon_frechet_states(frechet_specs, internal_codon, min_fraction)

    if not states:
        # Multiallelic same-position or uncombinable input: single-SNP fallback.
        return _combined_fallback_single_snp(variants, feature)

    accepted = [s for s in states if s.accepted]
    if not accepted:
        # No accepted combined state for any member: single-SNP fallback.
        return _combined_fallback_single_snp(variants, feature)

    # The forced all-carried state (all members applied), if accepted and forced.
    all_carried = next(
        (s for s in accepted if len(s.member_indices) == len(members)
         and abs(s.forced_fraction - 1.0) <= _FRECHET_EPS),
        None,
    )

    annotations: list[AnnotatedVariant] = []
    for member_idx, (var, codon_pos, alt_base) in enumerate(members):
        # Single-exchange codon: apply only this member's SNP.
        single_bases = list(internal_codon)
        single_bases[codon_pos] = alt_base
        single_codon = ''.join(single_bases)
        single_aa = translate_codon(single_codon)

        # Check whether this member's single-exchange state is Fréchet-rejected.
        single_state = next(
            (s for s in states
             if len(s.member_indices) == 1 and s.member_indices[0] == member_idx),
            None,
        )
        single_rejected = single_state is None or single_state.lower <= _FRECHET_EPS

        # Forced-overlap exception: replace single with the forced all-carried state.
        promoted = False
        if single_rejected and all_carried is not None:
            single_codon = all_carried.alt_codon
            single_aa = all_carried.alt_aa
            promoted = True

        consequence = _classify_snp_consequence(ref_aa, single_aa, codon_idx)

        # combined_states: accepted states that include this member, deduped by
        # alt_aa with lower summed. Drop the promoted all-carried state (it is now
        # the single) to avoid showing the same effect twice.
        member_states = [
            s for s in accepted
            if member_idx in s.member_indices
            and not (promoted and s is all_carried)
        ]
        combined_states = _dedupe_states_by_aa(member_states)

        annotations.append(AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=internal_codon,
            alt_codon=single_codon,
            ref_aa=ref_aa,
            alt_aa=single_aa,
            consequence=consequence,
            is_combined_codon_event=True,
            combined_member_count=len(members),
            combined_states=combined_states,
        ))

    return annotations


def _dedupe_states_by_aa(states: list[CodonState]) -> list[CodonState]:
    """Deduplicate CodonStates by alt_aa, summing lower for duplicates."""
    by_aa: dict[str, CodonState] = {}
    for s in states:
        if s.alt_aa in by_aa:
            prev = by_aa[s.alt_aa]
            by_aa[s.alt_aa] = CodonState(
                alt_codon=prev.alt_codon,
                alt_aa=prev.alt_aa,
                lower=prev.lower + s.lower,
                upper=max(prev.upper, s.upper),
                forced_fraction=max(prev.forced_fraction, s.forced_fraction),
                accepted=True,
                member_indices=prev.member_indices,
            )
        else:
            by_aa[s.alt_aa] = s
    return list(by_aa.values())


def _combined_fallback_single_snp(
    variants: list[VariantCall],
    feature: FeatureRecord,
) -> list[AnnotatedVariant]:
    """Emit plain single-SNP annotations for each member (no combined_states)."""
    seq_cds = feature.nt_sequence.upper()
    cds_codons = [list(seq_cds[i:i + 3]) for i in range(feature.codon_start, len(seq_cds), 3)]
    annotations: list[AnnotatedVariant] = []
    for var in sorted(variants, key=lambda v: v.pos):
        cds_pos = feature.genomic_to_cds_position(var.pos)
        if cds_pos is None:
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        coding_pos = cds_pos - feature.codon_start
        if coding_pos < 0:
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        mut_codon_idx = coding_pos // 3
        frame_offset = coding_pos % 3
        if mut_codon_idx >= len(cds_codons):
            annotations.append(AnnotatedVariant(variant=var, feature_name=feature.name))
            continue
        mut = reverse_complement(var.alt) if feature.strand == '-' else var.alt
        ann = _annotate_snp(var, feature, cds_codons, mut_codon_idx, frame_offset, mut)
        annotations.append(ann)
    return annotations


def _annotate_variant_in_feature(
    var: VariantCall,
    feature: FeatureRecord,
) -> list[AnnotatedVariant]:
    """
    Annotate a single variant within a feature.

    Handles SNPs, in-frame insertions, in-frame deletions, frameshifts, and mid-codon in-frame
    indels (split into missense + indel annotations).

    :return: list of AnnotatedVariant; empty list when the variant is a skipped non-SNP
    """
    seq_cds = feature.nt_sequence.upper()
    if not seq_cds:
        return [AnnotatedVariant(variant=var, feature_name=feature.name)]

    coding_nt = seq_cds[feature.codon_start:]

    cds_variant_pos = feature.genomic_to_cds_position(var.pos)
    if cds_variant_pos is None:
        return [AnnotatedVariant(variant=var, feature_name=feature.name)]

    # codon_start can place CDS after a non-coding prefix in feature.nt_sequence,
    # so this remaps absolute CDS coordinates into the translated coding frame.
    coding_variant_pos = cds_variant_pos - feature.codon_start
    if coding_variant_pos < 0:
        return [AnnotatedVariant(variant=var, feature_name=feature.name)]

    codon_idx = coding_variant_pos // 3
    frame_offset = coding_variant_pos % 3

    if _is_snp(var.ref, var.alt):
        cds_codons = [list(coding_nt[i:i + 3]) for i in range(0, len(coding_nt), 3)]
        if codon_idx >= len(cds_codons):
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        mut = reverse_complement(var.alt) if feature.strand == '-' else var.alt
        return [_annotate_snp(var, feature, cds_codons, codon_idx, frame_offset, mut)]

    if _is_insertion(var.ref, var.alt):
        indel_anchor_pos = _indel_anchor_coding_pos(coding_variant_pos, len(var.ref), feature.strand)
        if indel_anchor_pos < 0:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        return _annotate_insertion(
            var,
            feature,
            coding_nt,
            indel_anchor_pos // 3,
            indel_anchor_pos % 3,
        )

    if _is_deletion(var.ref, var.alt):
        indel_anchor_pos = _indel_anchor_coding_pos(coding_variant_pos, len(var.ref), feature.strand)
        if indel_anchor_pos < 0:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        return _annotate_deletion(
            var,
            feature,
            coding_nt,
            indel_anchor_pos // 3,
            indel_anchor_pos % 3,
        )

    return []


def _annotate_snp(
    var: VariantCall,
    feature: FeatureRecord,
    cds_codons: list[list[str]],
    mut_codon_idx: int,
    codon_pos: int,
    mut: str,
) -> AnnotatedVariant:
    """
    Annotate a single nucleotide substitution.

    The reference amino acid is always derived from the internal CDS.
    If a valid query codon is present, the alternate amino acid is derived
    from that codon; otherwise internal CDS codon context is used.
    """
    internal_codon = ''.join(cds_codons[mut_codon_idx])
    ref_aa = translate_codon(internal_codon)

    affected_codon = _resolve_anchor_codon(var, internal_codon)

    alt_codon_bases = list(affected_codon)
    alt_codon_bases[codon_pos] = mut
    alt_codon_str = ''.join(alt_codon_bases)
    alt_aa = translate_codon(alt_codon_str)

    consequence = _classify_snp_consequence(ref_aa, alt_aa, mut_codon_idx)

    return AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=mut_codon_idx,
        ref_codon=affected_codon,
        alt_codon=alt_codon_str,
        ref_aa=ref_aa,
        alt_aa=alt_aa,
        consequence=consequence,
    )


def _classify_snp_consequence(ref_aa: str, alt_aa: str, codon_idx: int) -> str:
    """Classify the amino acid consequence of a single nucleotide substitution."""
    if ref_aa == '?' or alt_aa == '?':
        return 'unknown'
    if ref_aa == alt_aa:
        return 'synonymous'
    if codon_idx == 0 and ref_aa == 'M':
        return 'start_lost'
    if alt_aa == '*' and ref_aa != '*':
        return 'stop_gained'
    if ref_aa == '*':
        return 'stop_loss'
    return 'missense'


def _annotate_frameshift(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
) -> AnnotatedVariant:
    """
    Annotate a frameshift indel.

    Records the anchor codon amino acid; alt_aa follows anchored frameshift
    nomenclature (e.g. ``KfsX``).

    :param var: variant call
    :param feature: feature record
    :param coding_nt: coding nucleotide sequence (from codon_start onward)
    :param codon_idx: 0-based codon index of the anchor base
    :return: AnnotatedVariant with consequence='frameshift'
    """
    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    anchor_codon = _resolve_anchor_codon(var, internal_codon)
    anchor_aa = translate_codon(anchor_codon)
    return AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=anchor_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=f'{anchor_aa}fsX',
        consequence='frameshift',
    )


def _annotate_insertion(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
    frame_offset: int,
) -> list[AnnotatedVariant]:
    """
    Annotate an in-frame insertion or frameshift insertion.

    Non-in-frame insertions are always annotated as frameshift.
    In-frame insertions whose anchor is at a codon boundary (frame_offset == 2) are
    annotated as insertion. Mid-codon in-frame insertions are split into a missense
    annotation (if the anchor codon changes) plus an insertion annotation.

    :param var: variant call; ALT uses VCF anchor-base convention (alt[1:] are inserted bases)
    :param feature: feature record
    :param coding_nt: coding nucleotide sequence (from codon_start onward)
    :param codon_idx: 0-based codon index of the anchor base
    :param frame_offset: position of anchor base within its codon (0, 1, or 2)
    :return: list of AnnotatedVariant
    """
    if abs(len(var.alt) - len(var.ref)) % 3 != 0:
        return [_annotate_frameshift(var, feature, coding_nt, codon_idx)]

    if not _is_vcf_anchor_at_codon_boundary(frame_offset):
        return _split_mid_codon_insertion(var, feature, coding_nt, codon_idx, frame_offset)

    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    anchor_codon = _resolve_anchor_codon(var, internal_codon)
    anchor_aa = translate_codon(anchor_codon)
    inserted_bases = var.alt[1:]  # strip anchor base
    inserted_aas = _translate_indel_bases(inserted_bases, feature.strand)

    return [AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=anchor_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=anchor_aa + inserted_aas,
        consequence='insertion',
    )]


def _annotate_deletion(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
    frame_offset: int,
) -> list[AnnotatedVariant]:
    """
    Annotate an in-frame deletion or frameshift deletion.

    Non-in-frame deletions are always annotated as frameshift.
    In-frame deletions whose anchor is at a codon boundary (frame_offset == 2) are
    annotated as deletion. Mid-codon in-frame deletions are split into a missense
    annotation (if the anchor codon changes) plus a deletion annotation.

    :param var: variant call; REF uses VCF anchor-base convention (ref[1:] are deleted bases)
    :param feature: feature record
    :param coding_nt: coding nucleotide sequence (from codon_start onward)
    :param codon_idx: 0-based codon index of the anchor base
    :param frame_offset: position of anchor base within its codon (0, 1, or 2)
    :return: list of AnnotatedVariant
    """
    if abs(len(var.alt) - len(var.ref)) % 3 != 0:
        return [_annotate_frameshift(var, feature, coding_nt, codon_idx)]

    if not _is_vcf_anchor_at_codon_boundary(frame_offset):
        return _split_mid_codon_deletion(var, feature, coding_nt, codon_idx, frame_offset)

    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    anchor_codon = _resolve_anchor_codon(var, internal_codon)
    anchor_aa = translate_codon(anchor_codon)
    deleted_bases = var.ref[1:]  # strip anchor base
    deleted_aas = _translate_indel_bases(deleted_bases, feature.strand)

    return [AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=anchor_codon,
        alt_codon='',
        ref_aa=anchor_aa + deleted_aas,
        alt_aa=anchor_aa,
        consequence='deletion',
    )]


def _split_mid_codon_insertion(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
    frame_offset: int,
) -> list[AnnotatedVariant]:
    """
    Split a mid-codon in-frame insertion into missense + insertion annotations.

    When an in-frame insertion starts mid-codon, the anchor codon is partially rewritten.
    This function reconstructs the query anchor codon and emits up to 2 annotations:
    a missense/synonymous/stop_gained/stop_loss/start_lost for the anchor codon change
    (skipped if synonymous), and an insertion for the inserted amino acids.

    :param var: variant call; ALT uses VCF anchor-base convention
    :param feature: feature record
    :param coding_nt: coding nucleotide sequence (from codon_start onward)
    :param codon_idx: 0-based codon index of the anchor base
    :param frame_offset: position of anchor base within its codon (0 or 1)
    :return: list of 1 or 2 AnnotatedVariant
    """
    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    ref_codon = _resolve_anchor_codon(var, internal_codon)
    ref_aa = translate_codon(ref_codon)
    preserved = frame_offset + 1

    # Get inserted bases in coding orientation
    inserted_bases_genomic = var.alt[1:]
    inserted_coding = reverse_complement(inserted_bases_genomic) if feature.strand == '-' else inserted_bases_genomic.upper()

    # Reconstruct query anchor codon: preserved bases from ref + (3-preserved) bases from inserted
    suffix_len = 3 - preserved
    query_codon = ref_codon[:preserved] + inserted_coding[:suffix_len]
    query_aa = translate_codon(query_codon)

    results: list[AnnotatedVariant] = []

    # Anchor codon annotation (missense/synonymous/stop_gained/stop_loss/start_lost)
    if ref_aa != query_aa:
        consequence = _classify_snp_consequence(ref_aa, query_aa, codon_idx)
        results.append(AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=ref_codon,
            alt_codon=query_codon,
            ref_aa=ref_aa,
            alt_aa=query_aa,
            consequence=consequence,
        ))

    # Insertion payload: remaining inserted bases + displaced bases from end of ref anchor codon
    payload_bases = inserted_coding[suffix_len:] + ref_codon[preserved:]
    inserted_aas = str(Seq(payload_bases).translate()) if len(payload_bases) >= 3 else ''

    # Insertion annotation uses the anchor AA from the reference codon
    anchor_aa = ref_aa
    results.append(AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=anchor_aa + inserted_aas,
        consequence='insertion',
    ))

    return results


def _split_mid_codon_deletion(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
    frame_offset: int,
) -> list[AnnotatedVariant]:
    """
    Split a mid-codon in-frame deletion into missense + deletion annotations.

    When an in-frame deletion starts mid-codon, the anchor codon is partially rewritten.
    This function reconstructs the query anchor codon from the reference CDS after the
    deleted region and emits up to 2 annotations: a missense/synonymous/stop_gained/
    stop_loss/start_lost for the anchor codon change (skipped if synonymous), and a
    deletion for the removed amino acids.

    :param var: variant call; REF uses VCF anchor-base convention
    :param feature: feature record
    :param coding_nt: coding nucleotide sequence (from codon_start onward)
    :param codon_idx: 0-based codon index of the anchor base
    :param frame_offset: position of anchor base within its codon (0 or 1)
    :return: list of 1 or 2 AnnotatedVariant
    """
    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    ref_codon = _resolve_anchor_codon(var, internal_codon)
    ref_aa = translate_codon(ref_codon)
    preserved = frame_offset + 1

    deleted_bases = var.ref[1:]  # strip anchor base
    suffix_len = 3 - preserved

    # Position in CDS immediately after the deleted region
    post_delete_pos = codon_idx * 3 + preserved + len(deleted_bases)

    # Check CDS bounds: need suffix_len bases after the deletion
    if post_delete_pos + suffix_len > len(coding_nt):
        logger.warning(
            'Mid-codon deletion at codon %d extends beyond CDS; falling back to deletion with unknown AA change',
            codon_idx,
        )
        return [AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=ref_codon,
            alt_codon='',
            ref_aa=ref_aa,
            alt_aa='?',
            consequence='deletion',
        )]

    # Reconstruct query anchor codon: preserved bases from ref + suffix from CDS after deletion
    query_codon = ref_codon[:preserved] + coding_nt[post_delete_pos:post_delete_pos + suffix_len]
    query_aa = translate_codon(query_codon)

    results: list[AnnotatedVariant] = []

    # Anchor codon annotation
    if ref_aa != query_aa:
        consequence = _classify_snp_consequence(ref_aa, query_aa, codon_idx)
        results.append(AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=ref_codon,
            alt_codon=query_codon,
            ref_aa=ref_aa,
            alt_aa=query_aa,
            consequence=consequence,
        ))

    # Deleted AAs: translate the removed codons from the reference CDS
    n_removed = len(deleted_bases) // 3
    deleted_codons = coding_nt[(codon_idx + 1) * 3:(codon_idx + 1 + n_removed) * 3]
    deleted_aas = _translate_indel_bases(deleted_codons, '+')  # already in coding orientation

    # Deletion annotation uses the anchor AA from the reference codon
    anchor_aa = ref_aa
    results.append(AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=anchor_aa + deleted_aas,
        alt_aa=anchor_aa,
        consequence='deletion',
    ))

    return results


def _resolve_anchor_codon(var: VariantCall, internal_codon: str) -> str:
    """Use query codon context when valid, otherwise use internal CDS codon."""
    query_codon = var.query_ref_codon.upper()
    if len(query_codon) == 3 and '-' not in query_codon:
        return query_codon
    return internal_codon


def _is_vcf_anchor_at_codon_boundary(frame_offset: int) -> bool:
    """
    Return True when a VCF anchor sits on a codon boundary in coding orientation.

    VCF stores the nucleotide immediately before an indel in genomic 5'->3' order.
    After remapping into coding orientation, codon-boundary anchors are represented
    consistently as frame offset 2.
    """
    return frame_offset == 2


def _indel_anchor_coding_pos(coding_variant_pos: int, ref_len: int, strand: str) -> int:
    """
    Return coding-position anchor for VCF indels.

    On '+' features, VCF anchor already refers to the coding-preceding nucleotide.
    On '-' features, genomic 5'->3' VCF anchors are downstream in coding orientation,
    so the coding anchor shifts left by the REF length.
    """
    if strand == '+':
        return coding_variant_pos
    # On '-' features, VCF's left-anchor in genomic space is rightward in coding space;
    # shifting by REF length realigns to the coding-preceding anchor nucleotide.
    return coding_variant_pos - ref_len


def normalize_mutation(
    raw: str,
    *,
    reference: str = '',
    position_1based: int | None = None,
) -> str | None:
    """
    Normalise a raw mutation token from a rules TSV to the canonical DB form.

    The canonical tokens are:

    - ``A``        — specific alt amino acid (missense, synonymous, stop-loss target)
    - ``*``        — stop gained
    - ``fsX``      — frameshift at this codon
    - ``F50FGG``   — insertion: insertion after F50 resulting in ``FGG``
    - ``FGG50F``   — deletion: deletion from ``FGG`` to ``F`` at anchor position 50

    The function accepts full notation and common flexible input forms used in
    resistance tables. It normalizes to the project notation above.

    Bare ``*`` means stop-gained and already follows the canonical nomenclature.

    :param raw: raw string from the mutation column of a rules TSV
    :param reference: optional reference AA from the rules row
    :param position_1based: optional 1-based AA position from the rules row
    :return: canonical token, or None if the input cannot be recognised
    """


    s = raw.strip()
    if not s:
        return None

    s_upper = s.upper()
    ref = reference.strip().upper()

    # Stop word: F67stop, F67STOP
    if _RE_STOP_FULL.match(s):
        return '*'

    # Frameshift: fs, fsX, F67fs, F67frameshift, F67fsATFF*
    if _RE_FS_ANY.match(s):
        return 'fsX'

    # HGVS-like insertion, e.g. F50insGG or F50_F51insGG -> F50FGG
    m_ins = _RE_HGVS_INS.match(s)
    if m_ins:
        left, pos, inserted = m_ins.groups()
        return f'{left.upper()}{pos}{left.upper()}{inserted.upper()}'

    # HGVS-like deletion with explicit deleted sequence.
    # F50delGG -> FGG50F.
    m_del = _RE_HGVS_DEL.match(s)
    if m_del:
        left, pos, deleted = m_del.groups()
        if deleted:
            return f'{left.upper()}{deleted.upper()}{pos}{left.upper()}'
        return None

    # Canonical rewrite style and full substitutions (F67L / F50FGG / FGG50F)
    m_rw = _RE_REWRITE.match(s)
    if m_rw:
        left, pos, right = m_rw.groups()
        left_u = left.upper()
        right_u = right.upper()

        # Substitution / stop-gained / stop-loss -> keep stored single alt token.
        if len(left_u) == 1 and len(right_u) == 1:
            return right_u

        # Insertion: left anchor is preserved and right expands it.
        if right_u.startswith(left_u) and len(right_u) > len(left_u):
            return f'{left_u}{pos}{right_u}'

        # Deletion: left side contracts to right anchor.
        if left_u.startswith(right_u) and len(left_u) > len(right_u):
            return f'{left_u}{pos}{right_u}'

        return None

    # Prefix deletion notation is ambiguous and therefore rejected.
    if _RE_DEL_PREFIX.match(s):
        return None

    # ── Bare tokens ───────────────────────────────────────────────────────────

    # Bare stop token/word
    if s == '*' or s_upper == 'STOP':
        return '*'

    # Generic insertion wildcard token — matches any in-frame insertion at this position.
    if s_upper == 'INS_ANY':
        return 'INS_any'

    # Bare insertion, e.g. insGG -> requires row context.
    if s_upper.startswith('INS'):
        inserted = s[3:].strip().upper()
        if inserted and ref and position_1based is not None:
            return f'{ref}{position_1based}{ref}{inserted}'
        return None

    # Frameshift: fs, fsX, fsATGG*, frameshift, …
    if s_upper.startswith('FS') or s_upper.startswith('FRAMESHIFT'):
        return 'fsX'

    # Bare deletion token is ambiguous and therefore rejected.
    if s_upper == 'DEL':
        return None

    # Single amino acid letter
    if _RE_BARE_AA.match(s):
        if s_upper == 'X':
            return None
        return s_upper

    return None


def classify_similarity(
    observed_aa: str,
    rule_aa: str,
    high_threshold: int = 1,
    moderate_threshold: int = 0,
) -> str:
    """
    Classify amino acid similarity based on BLOSUM62 score.

    Thresholds:
    - score >= 1  → 'high'     (biochemically similar substitution)
    - score >= 0  → 'moderate' (neutral substitution)
    - score < 0   → 'low'     (dissimilar substitution)

    :param observed_aa: observed alternate amino acid
    :param rule_aa: amino acid from the resistance rule
    :param high_threshold: score threshold for high similarity
    :param moderate_threshold: score threshold for moderate similarity
    :return: similarity class string
    """
    try:
        score = _BLOSUM62[observed_aa.upper(), rule_aa.upper()]
    except (KeyError, IndexError):
        # Non-standard tokens (e.g. 'fsX', '*') are not in the matrix
        logger.debug('BLOSUM62 matrix does not contain %s/%s — defaulting to low', observed_aa, rule_aa)
        return 'low'
    if score >= high_threshold:
        return 'high'
    if score >= moderate_threshold:
        return 'moderate'
    return 'low'


def assign_af_bins(
    annotations: list[AnnotatedVariant],
    bins: dict[str, tuple[float, float]],
) -> list[AnnotatedVariant]:
    """
    Assign an allele-frequency bin label to each annotated variant.

    Mutates ``af_bin`` in place and returns the same list.

    :param annotations: annotated variants to bin
    :param bins: mapping of bin label to (lower_inclusive, upper_inclusive)
    :return: the same annotations list with af_bin populated
    """

    # Sort bins by lower bound descending so higher bins are checked first
    sorted_bins = sorted(bins.items(), key=lambda x: -x[1][0])

    for ann in annotations:
        af = ann.variant.allele_freq
        for label, (lo, hi) in sorted_bins:
            if lo <= af <= hi:
                ann.af_bin = label

    return annotations
