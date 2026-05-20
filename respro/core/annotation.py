"""
Codon-aware variant annotation — amino acid consequence classification, mutation normalisation,
and amino acid similarity scoring.
"""

from __future__ import annotations

import logging
import re

from Bio.Align.substitution_matrices import load as _load_matrix
from Bio.Seq import Seq

from respro.db.models import AnnotatedVariant, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

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
    snp_combine_af_threshold: float = 0.75,
    is_fasta_mode: bool = False,
) -> list[AnnotatedVariant]:
    """
    Annotate a list of variants with codon-aware amino acid consequences.

    Handles SNPs, in-frame insertions, in-frame deletions, frameshifts, and in-frame complex
    indels in CDS regions. Only variants outside any CDS are skipped (included with empty feature_name).

    SNP consequences can use a query codon from FASTA-based remapping when
    available (``VariantCall.query_ref_codon``).

    :param variants: parsed variant calls (0-based positions). Variants with no CDS hit are
        included with empty feature_name. If two or more SNPs in the same feature codon all have
        AF > threshold, they are annotated as one combined codon event.
    :param features: feature annotations for the reference

    :param snp_combine_af_threshold: strict AF threshold for combining SNPs
        within one codon (must be greater than this value)
    :param is_fasta_mode: mark emitted annotations as FASTA-derived
    :return: list of AnnotatedVariant
    """
    results: list[AnnotatedVariant] = []
    skipped_non_snp = 0
    group_plan = _plan_combined_snp_groups(variants, features, snp_combine_af_threshold)

    for var_idx, var in enumerate(variants):
        matching_features = [f for f in features if f.contains(var.pos)]
        if not matching_features:
            results.append(AnnotatedVariant(variant=var, is_fasta_mode=is_fasta_mode))
            continue

        for feature in matching_features:
            codon_idx = feature.codon_index(var.pos)
            codon_key = (feature.id, codon_idx)
            group = group_plan.get(codon_key)
            if group is not None:
                if var_idx == group[0]:
                    members = [variants[i] for i in group]
                    combined_annotation = _annotate_combined_snp_codon(members, feature)
                    combined_annotation.is_fasta_mode = is_fasta_mode
                    results.append(combined_annotation)
                continue
            ann = _annotate_variant_in_feature(var, feature)
            if ann is None:
                skipped_non_snp += 1
                continue
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


def _is_inframe(ref: str, alt: str) -> bool:
    """Return True when the indel length is a multiple of 3."""
    return abs(len(alt) - len(ref)) % 3 == 0


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


def _plan_combined_snp_groups(
    variants: list[VariantCall],
    features: list[FeatureRecord],
    threshold: float,
) -> dict[tuple[int, int], list[int]]:
    """
    Return codon groups that should be annotated as one combined SNP event.

    :param variants: input variant list
    :param features: feature records
    :param threshold: strict AF threshold for SNP combination
    :return: {(feature_id, codon_idx): [variant_index, ...]}
    """
    grouped: dict[tuple[int, int], list[int]] = {}
    for idx, var in enumerate(variants):
        if not _is_snp(var.ref, var.alt):
            continue
        for feature in features:
            if not feature.contains(var.pos):
                continue
            # Group SNPs per feature-codon so linked high-AF changes can be evaluated jointly.
            codon_idx = feature.codon_index(var.pos)
            if codon_idx is None or codon_idx < 0:
                continue
            key = (feature.id, codon_idx)
            grouped.setdefault(key, []).append(idx)

    planned: dict[tuple[int, int], list[int]] = {}
    for key, member_indices in grouped.items():
        if len(member_indices) < 2:
            continue
        members = [variants[i] for i in member_indices]
        # Strict threshold: only treat as one codon event when all SNPs are high-AF.
        if not all(v.allele_freq > threshold for v in members):
            continue
        planned[key] = sorted(member_indices)
    return planned


def _annotate_combined_snp_codon(
    variants: list[VariantCall],
    feature: FeatureRecord,
) -> AnnotatedVariant:
    """
    Annotate multiple SNPs in one codon as a single codon event.

    :param variants: SNPs from the same codon (same feature)
    :param feature: feature containing the codon
    :return: one combined annotation
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

    query_codons = {v.query_ref_codon.upper() for v in variants if len(v.query_ref_codon) == 3}
    # Only use query codon when all members agree on one context; otherwise stay internal.
    affected_codon = next(iter(query_codons)) if len(query_codons) == 1 else internal_codon

    alt_codon_bases = list(affected_codon)
    seen: dict[int, str] = {}
    for var in sorted(variants, key=lambda v: v.pos):
        codon_pos = feature.codon_position_in_codon(var.pos)
        if codon_pos is None:
            raise ValueError(
                f'Combined SNP annotation requires coding codon position for feature {feature.name!r} '
                f'at genomic position {var.pos}'
            )
        alt_base = reverse_complement(var.alt) if feature.strand == '-' else var.alt.upper()
        # Conflicting ALTs at the same codon base indicate inconsistent input; fail fast.
        if codon_pos in seen and seen[codon_pos] != alt_base:
            raise ValueError(
                f'Conflicting SNPs in same codon for feature {feature.name!r} at codon {codon_idx + 1}'
            )
        seen[codon_pos] = alt_base
        alt_codon_bases[codon_pos] = alt_base

    alt_codon = ''.join(alt_codon_bases)
    alt_aa = translate_codon(alt_codon)
    consequence = _classify_snp_consequence(ref_aa, alt_aa, codon_idx)

    # Conservative combined AF: lower bound of the linked SNP set.
    combined_var = VariantCall(
        chrom=anchor.chrom,
        pos=anchor.pos,
        ref=anchor.ref,
        alt=anchor.alt,
        allele_freq=min(v.allele_freq for v in variants),
        depth=anchor.depth,
        filter_status=anchor.filter_status,
        query_ref_codon=affected_codon if len(affected_codon) == 3 else '',
    )

    return AnnotatedVariant(
        variant=combined_var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=affected_codon,
        alt_codon=alt_codon,
        ref_aa=ref_aa,
        alt_aa=alt_aa,
        consequence=consequence,
        is_combined_codon_event=True,
        combined_member_count=len(variants),
    )


def _annotate_variant_in_feature(
    var: VariantCall,
    feature: FeatureRecord,
) -> AnnotatedVariant | None:
    """
    Annotate a single variant within a feature.

    Handles SNPs, in-frame insertions, in-frame deletions, frameshifts, and mid-codon in-frame
    indels (annotated as inframe_complex).
    """
    seq_cds = feature.nt_sequence.upper()
    if not seq_cds:
        return AnnotatedVariant(variant=var, feature_name=feature.name)

    coding_nt = seq_cds[feature.codon_start:]

    cds_variant_pos = feature.genomic_to_cds_position(var.pos)
    if cds_variant_pos is None:
        return AnnotatedVariant(variant=var, feature_name=feature.name)

    coding_variant_pos = cds_variant_pos - feature.codon_start
    if coding_variant_pos < 0:
        return AnnotatedVariant(variant=var, feature_name=feature.name)

    codon_idx = coding_variant_pos // 3
    frame_offset = coding_variant_pos % 3

    if _is_snp(var.ref, var.alt):
        cds_codons = [list(coding_nt[i:i + 3]) for i in range(0, len(coding_nt), 3)]
        if codon_idx >= len(cds_codons):
            return AnnotatedVariant(variant=var, feature_name=feature.name)
        mut = reverse_complement(var.alt) if feature.strand == '-' else var.alt
        return _annotate_snp(var, feature, cds_codons, codon_idx, frame_offset, mut)

    if _is_insertion(var.ref, var.alt):
        indel_anchor_pos = _indel_anchor_coding_pos(coding_variant_pos, len(var.ref), feature.strand)
        if indel_anchor_pos < 0:
            return AnnotatedVariant(variant=var, feature_name=feature.name)
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
            return AnnotatedVariant(variant=var, feature_name=feature.name)
        return _annotate_deletion(
            var,
            feature,
            coding_nt,
            indel_anchor_pos // 3,
            indel_anchor_pos % 3,
        )

    return None


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
) -> AnnotatedVariant:
    """
    Annotate an in-frame insertion or frameshift insertion.

    Non-in-frame insertions are always annotated as frameshift.
    In-frame insertions whose anchor is at a codon boundary (frame_offset == 2) are
    annotated as insertion. Mid-codon in-frame insertions are annotated as inframe_complex
    because two neighbouring codons are partially rewritten; the AA consequence is not
    resolvable to a single canonical token.

    :param var: variant call; ALT uses VCF anchor-base convention (alt[1:] are inserted bases)
    :param feature: feature record
    :param coding_nt: coding nucleotide sequence (from codon_start onward)
    :param codon_idx: 0-based codon index of the anchor base
    :param frame_offset: position of anchor base within its codon (0, 1, or 2)
    :return: AnnotatedVariant
    """
    if not _is_inframe(var.ref, var.alt):
        return _annotate_frameshift(var, feature, coding_nt, codon_idx)

    if not _is_vcf_anchor_at_codon_boundary(frame_offset):
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
            alt_aa='?',
            consequence='inframe_complex',
        )

    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    anchor_codon = _resolve_anchor_codon(var, internal_codon)
    anchor_aa = translate_codon(anchor_codon)
    inserted_bases = var.alt[1:]  # strip anchor base
    inserted_aas = _translate_indel_bases(inserted_bases, feature.strand)

    return AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=anchor_codon,
        alt_codon='',
        ref_aa=anchor_aa,
        alt_aa=anchor_aa + inserted_aas,
        consequence='insertion',
    )


def _annotate_deletion(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
    frame_offset: int,
) -> AnnotatedVariant:
    """
    Annotate an in-frame deletion or frameshift deletion.

    Non-in-frame deletions are always annotated as frameshift.
    In-frame deletions whose anchor is at a codon boundary (frame_offset == 2) are
    annotated as deletion. Mid-codon in-frame deletions are annotated as inframe_complex
    because two neighbouring codons are partially rewritten; the AA consequence is not
    resolvable to a single canonical token.

    :param var: variant call; REF uses VCF anchor-base convention (ref[1:] are deleted bases)
    :param feature: feature record
    :param coding_nt: coding nucleotide sequence (from codon_start onward)
    :param codon_idx: 0-based codon index of the anchor base
    :param frame_offset: position of anchor base within its codon (0, 1, or 2)
    :return: AnnotatedVariant
    """
    if not _is_inframe(var.ref, var.alt):
        return _annotate_frameshift(var, feature, coding_nt, codon_idx)

    if not _is_vcf_anchor_at_codon_boundary(frame_offset):
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
            alt_aa='?',
            consequence='inframe_complex',
        )

    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    anchor_codon = _resolve_anchor_codon(var, internal_codon)
    anchor_aa = translate_codon(anchor_codon)
    deleted_bases = var.ref[1:]  # strip anchor base
    deleted_aas = _translate_indel_bases(deleted_bases, feature.strand)

    return AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=anchor_codon,
        alt_codon='',
        ref_aa=anchor_aa + deleted_aas,
        alt_aa=anchor_aa,
        consequence='deletion',
    )


def _resolve_anchor_codon(var: VariantCall, internal_codon: str) -> str:
    """Use query codon context when valid, otherwise use internal CDS codon."""
    query_codon = var.query_ref_codon.upper()
    return query_codon if len(query_codon) == 3 else internal_codon


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


def classify_similarity(observed_aa: str, rule_aa: str) -> str:
    """
    Classify amino acid similarity based on BLOSUM62 score.

    Thresholds:
    - score >= 1  → 'high'     (biochemically similar substitution)
    - score >= 0  → 'moderate' (neutral substitution)
    - score < 0   → 'low'     (dissimilar substitution)

    :param observed_aa: observed alternate amino acid
    :param rule_aa: amino acid from the resistance rule
    :return: similarity class string
    """
    try:
        score = _load_matrix('BLOSUM62')[observed_aa.upper(), rule_aa.upper()]
    except (KeyError, IndexError):
        # Non-standard tokens (e.g. 'fsX', '*') are not in the matrix
        logger.debug('BLOSUM62 matrix does not contain %s/%s — defaulting to low', observed_aa, rule_aa)
        return 'low'
    if score >= 1:
        return 'high'
    if score >= 0:
        return 'moderate'
    return 'low'


def assign_af_bins(
    annotations: list[AnnotatedVariant],
    bins: dict[str, tuple[float, float]] | None = None,
) -> list[AnnotatedVariant]:
    """
    Assign an allele-frequency bin label to each annotated variant.

    Mutates ``af_bin`` in place and returns the same list.

    :param annotations: annotated variants to bin
    :param bins: mapping of bin label to (lower_inclusive, upper_inclusive);
        defaults to the built-in high/intermediate/low bins
    :return: the same annotations list with af_bin populated
    """
    if bins is None:
        bins = {
            'high': (0.75, 1.0),
            'intermediate': (0.25, 0.7499),
            'low': (0.01, 0.2499),
        }

    # Sort bins by lower bound descending so higher bins are checked first
    sorted_bins = sorted(bins.items(), key=lambda x: -x[1][0])

    for ann in annotations:
        af = ann.variant.allele_freq
        for label, (lo, hi) in sorted_bins:
            if lo <= af <= hi:
                ann.af_bin = label

    return annotations
