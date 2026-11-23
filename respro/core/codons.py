"""Pure codon-level sequence primitives — translation, consequence classification, and normalization."""

from __future__ import annotations

import logging
import re

from Bio.Align.substitution_matrices import load as _load_matrix
from Bio.Seq import Seq

from respro.db.models import AnnotatedVariant, FeatureRecord, VariantCall

logger = logging.getLogger(__name__)

_BLOSUM62 = _load_matrix('BLOSUM62')

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

_STANDARD_AA_LETTERS = frozenset('ACDEFGHIKLMNPQRSTVWY')

_RE_FS_ANY = re.compile(r'^(?:[A-Z*]\d+)?(?:fs|frameshift)', re.IGNORECASE)
_RE_STOP_FULL = re.compile(r'^[A-Z*]\d+stop$', re.IGNORECASE)
_RE_REWRITE = re.compile(r'^([A-Z*]+)(\d+)([A-Z*]+)$', re.IGNORECASE)
_RE_HGVS_INS = re.compile(r'^([A-Z*])(\d+)(?:_[A-Z*]\d+)?ins([A-Z*]+)$', re.IGNORECASE)
_RE_HGVS_DEL = re.compile(r'^([A-Z*])(\d+)(?:_[A-Z*]\d+)?del([A-Z*]+)?$', re.IGNORECASE)
_RE_DEL_PREFIX = re.compile(r'^del([A-Z*]+)?(?:[A-Z*]\d+|\d+)$', re.IGNORECASE)
_RE_BARE_AA = re.compile(r'^[A-Z]$', re.IGNORECASE)


def reverse_complement(seq: str) -> str:
    """Return the uppercase reverse complement of a DNA sequence."""
    return str(Seq(seq.upper()).reverse_complement())


def translate_codon(codon: str) -> str:
    """Translate a 3-base DNA codon to a 1-letter amino acid (``'*'`` stop, ``'?'`` invalid/ambiguous)."""
    codon = codon.upper()
    if len(codon) != 3:
        return '?'
    try:
        aa = str(Seq(codon).translate())
    except Exception as exc:
        logger.debug('Codon translation failed for %r: %s', codon, exc)
        return '?'
    return aa if aa else '?'


def is_snp(ref: str, alt: str) -> bool:
    return len(ref) == 1 and len(alt) == 1


def is_insertion(ref: str, alt: str) -> bool:
    return len(alt) > len(ref)


def is_deletion(ref: str, alt: str) -> bool:
    return len(ref) > len(alt)


def classify_snp_consequence(ref_aa: str, alt_aa: str, codon_idx: int) -> str:
    """Classify the amino-acid consequence of a codon substitution."""
    if ref_aa in ('?', 'X') or alt_aa in ('?', 'X'):
        return 'unknown'
    if ref_aa == alt_aa:
        return 'synonymous'
    if codon_idx == 0 and ref_aa == 'M':
        return 'start_lost'
    if alt_aa == '*' and ref_aa != '*':
        return 'stop_gained'
    if ref_aa == '*':
        return 'stop_lost'
    return 'missense'


def _translate_indel_bases(bases: str, strand: str) -> str:
    oriented = reverse_complement(bases) if strand == '-' else bases.upper()
    return str(Seq(oriented).translate())


_IUPAC_DNA_BASES = frozenset('ACGTURYKMSWBDHVN')


def _resolve_anchor_codon(
    var: VariantCall,
    internal_codon: str,
    feature_id: int = 0,
    *,
    require_acgt: bool = True,
) -> str:
    """Use ``var.query_ref_codon`` when valid and bound to ``feature_id``, else ``internal_codon``."""
    if var.query_codon_feature_id and var.query_codon_feature_id != feature_id:
        return internal_codon
    query_codon = var.query_ref_codon.upper()
    allowed = 'ACGT' if require_acgt else _IUPAC_DNA_BASES
    if len(query_codon) == 3 and all(base in allowed for base in query_codon):
        return query_codon
    return internal_codon


def _indel_anchor_coding_pos(coding_variant_pos: int, ref_len: int, strand: str) -> int:
    """Convert genomic 5'->3' VCF indel anchor into coding-preceding anchor offset."""
    # On '-' features, the genomic left-anchor sits downstream in coding space; shift left by len(REF).
    return coding_variant_pos if strand == '+' else coding_variant_pos - ref_len


def _trim_common_indel_padding(pos: int, ref: str, alt: str) -> tuple[int, str, str]:
    """Trim shared suffix/prefix padding on complex indels down to a 1-base VCF anchor."""
    ref_u, alt_u = ref.upper(), alt.upper()
    while len(ref_u) > 1 and len(alt_u) > 1 and ref_u[-1] == alt_u[-1]:
        ref_u = ref_u[:-1]
        alt_u = alt_u[:-1]
    while len(ref_u) > 1 and len(alt_u) > 1 and ref_u[0] == alt_u[0]:
        ref_u = ref_u[1:]
        alt_u = alt_u[1:]
        pos += 1
    return pos, ref_u, alt_u


def annotate_snp(
    var: VariantCall,
    feature: FeatureRecord,
    internal_codon: str,
    codon_idx: int,
    pos_in_codon: int,
    coding_alt_base: str,
) -> AnnotatedVariant:
    """Annotate a single-nucleotide substitution within a feature codon."""
    ref_aa = translate_codon(internal_codon)
    anchor_codon = _resolve_anchor_codon(var, internal_codon, feature.id, require_acgt=False)

    alt_bases = list(anchor_codon)
    alt_bases[pos_in_codon] = coding_alt_base
    alt_codon = ''.join(alt_bases)
    alt_aa = translate_codon(alt_codon)

    return AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=anchor_codon,
        alt_codon=alt_codon,
        ref_aa=ref_aa,
        alt_aa=alt_aa,
        consequence=classify_snp_consequence(ref_aa, alt_aa, codon_idx),
    )


def annotate_variant_in_feature(
    var: VariantCall,
    feature: FeatureRecord,
) -> list[AnnotatedVariant]:
    """Annotate one variant (SNP, frameshift, or in-frame indel) within ``feature``."""
    seq_cds = feature.nt_sequence.upper()
    if not seq_cds:
        return [AnnotatedVariant(variant=var, feature_name=feature.name)]

    coding_nt = seq_cds[feature.codon_start:]

    if is_snp(var.ref, var.alt):
        cds_pos = feature.genomic_to_cds_position(var.pos)
        if cds_pos is None:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        coding_pos = cds_pos - feature.codon_start
        if coding_pos < 0:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        codon_idx, pos_in_codon = divmod(coding_pos, 3)
        internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
        if not internal_codon:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        coding_alt = reverse_complement(var.alt) if feature.strand == '-' else var.alt.upper()
        return [annotate_snp(var, feature, internal_codon, codon_idx, pos_in_codon, coding_alt)]

    if is_insertion(var.ref, var.alt) or is_deletion(var.ref, var.alt):
        eff_pos, eff_ref, eff_alt = _trim_common_indel_padding(var.pos, var.ref, var.alt)
        cds_pos = feature.genomic_to_cds_position(eff_pos)
        if cds_pos is None:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        coding_pos = cds_pos - feature.codon_start
        if coding_pos < 0:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]

        anchor_pos = _indel_anchor_coding_pos(coding_pos, len(eff_ref), feature.strand)
        if anchor_pos < 0:
            if is_deletion(eff_ref, eff_alt) and anchor_pos == -1:
                if abs(len(eff_alt) - len(eff_ref)) % 3 != 0:
                    return [_annotate_frameshift(var, feature, coding_nt, 0)]
                if len(eff_alt) == 1 and eff_ref[0] == eff_alt[0]:
                    codon_0 = coding_nt[:3]
                    deleted_aas = _translate_indel_bases(eff_ref[1:], feature.strand)
                    return [AnnotatedVariant(
                        variant=var,
                        feature_name=feature.name,
                        codon_pos=0,
                        ref_codon=codon_0,
                        alt_codon='',
                        ref_aa=deleted_aas,
                        alt_aa='del',
                        consequence='deletion',
                    )]
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]

        codon_idx, frame_offset = divmod(anchor_pos, 3)
        if abs(len(eff_alt) - len(eff_ref)) % 3 != 0:
            return [_annotate_frameshift(var, feature, coding_nt, codon_idx)]
        if (len(eff_ref) > 1 and len(eff_alt) > 1) or eff_ref[0] != eff_alt[0]:
            return [AnnotatedVariant(variant=var, feature_name=feature.name)]
        if is_insertion(eff_ref, eff_alt):
            return _annotate_insertion(var, feature, coding_nt, codon_idx, frame_offset, eff_alt)
        return _annotate_deletion(var, feature, coding_nt, codon_idx, frame_offset, eff_ref)

    return []


def _annotate_frameshift(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
) -> AnnotatedVariant:
    internal_codon = coding_nt[codon_idx * 3:codon_idx * 3 + 3]
    anchor_codon = _resolve_anchor_codon(var, internal_codon, feature.id)
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
    eff_alt: str,
) -> list[AnnotatedVariant]:
    # frame_offset == 2 places the VCF anchor on the last base of its codon (clean codon boundary).
    if frame_offset != 2:
        return _split_mid_codon_insertion(var, feature, coding_nt, codon_idx, frame_offset, eff_alt)

    anchor_codon = _resolve_anchor_codon(var, coding_nt[codon_idx * 3:codon_idx * 3 + 3], feature.id)
    anchor_aa = translate_codon(anchor_codon)
    inserted_aas = _translate_indel_bases(eff_alt[1:], feature.strand)
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
    eff_ref: str,
) -> list[AnnotatedVariant]:
    if frame_offset != 2:
        return _split_mid_codon_deletion(var, feature, coding_nt, codon_idx, frame_offset, eff_ref)

    anchor_codon = _resolve_anchor_codon(var, coding_nt[codon_idx * 3:codon_idx * 3 + 3], feature.id)
    anchor_aa = translate_codon(anchor_codon)
    deleted_aas = _translate_indel_bases(eff_ref[1:], feature.strand)
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
    eff_alt: str,
) -> list[AnnotatedVariant]:
    """Split a mid-codon in-frame insertion into an optional anchor change plus an insertion."""
    ref_codon = _resolve_anchor_codon(var, coding_nt[codon_idx * 3:codon_idx * 3 + 3], feature.id)
    ref_aa = translate_codon(ref_codon)
    preserved = frame_offset + 1
    suffix_len = 3 - preserved

    inserted_raw = eff_alt[1:]
    inserted_coding = reverse_complement(inserted_raw) if feature.strand == '-' else inserted_raw.upper()

    query_codon = ref_codon[:preserved] + inserted_coding[:suffix_len]
    query_aa = translate_codon(query_codon)

    results: list[AnnotatedVariant] = []
    if ref_aa != query_aa:
        results.append(AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=ref_codon,
            alt_codon=query_codon,
            ref_aa=ref_aa,
            alt_aa=query_aa,
            consequence=classify_snp_consequence(ref_aa, query_aa, codon_idx),
        ))

    payload_bases = inserted_coding[suffix_len:] + ref_codon[preserved:]
    inserted_aas = str(Seq(payload_bases).translate()) if len(payload_bases) >= 3 else ''
    results.append(AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=ref_aa,
        alt_aa=ref_aa + inserted_aas,
        consequence='insertion',
    ))
    return results


def _split_mid_codon_deletion(
    var: VariantCall,
    feature: FeatureRecord,
    coding_nt: str,
    codon_idx: int,
    frame_offset: int,
    eff_ref: str,
) -> list[AnnotatedVariant]:
    """Split a mid-codon in-frame deletion into an optional anchor change plus a deletion."""
    ref_codon = _resolve_anchor_codon(var, coding_nt[codon_idx * 3:codon_idx * 3 + 3], feature.id)
    ref_aa = translate_codon(ref_codon)
    preserved = frame_offset + 1
    suffix_len = 3 - preserved
    deleted_bases = eff_ref[1:]
    post_delete_pos = codon_idx * 3 + preserved + len(deleted_bases)

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

    query_codon = ref_codon[:preserved] + coding_nt[post_delete_pos:post_delete_pos + suffix_len]
    query_aa = translate_codon(query_codon)

    results: list[AnnotatedVariant] = []
    if ref_aa != query_aa:
        results.append(AnnotatedVariant(
            variant=var,
            feature_name=feature.name,
            codon_pos=codon_idx,
            ref_codon=ref_codon,
            alt_codon=query_codon,
            ref_aa=ref_aa,
            alt_aa=query_aa,
            consequence=classify_snp_consequence(ref_aa, query_aa, codon_idx),
        ))

    n_removed = len(deleted_bases) // 3
    deleted_codons = coding_nt[(codon_idx + 1) * 3:(codon_idx + 1 + n_removed) * 3]
    deleted_aas = _translate_indel_bases(deleted_codons, '+')
    results.append(AnnotatedVariant(
        variant=var,
        feature_name=feature.name,
        codon_pos=codon_idx,
        ref_codon=ref_codon,
        alt_codon='',
        ref_aa=ref_aa + deleted_aas,
        alt_aa=ref_aa,
        consequence='deletion',
    ))
    return results


def normalize_mutation(
    raw: str,
    *,
    reference: str = '',
    position_1based: int | None = None,
) -> str | None:
    """Normalize a raw TSV mutation token into canonical DB notation, or ``None`` if unsupported.

    Canonical forms: single AA (``'A'``), stop (``'*'``), frameshift (``'fsX'``),
    wildcard insertion (``'INS_any'``), insertion (``'F50FGG'``), deletion (``'FGG50F'``).
    """
    s = raw.strip()
    if not s:
        return None
    s_upper = s.upper()
    ref = reference.strip().upper()

    if _RE_STOP_FULL.match(s) or s == '*' or s_upper == 'STOP':
        return '*'

    # Match unambiguous uppercase canonical AA indels (e.g. F50FS, FS50F) before
    # frameshift patterns so Phe-Ser indels are not misclassified as 'fsX'.
    m_rw = _RE_REWRITE.match(s)
    if m_rw:
        left_raw, pos, right_raw = m_rw.groups()
        if (
            left_raw == left_raw.upper()
            and right_raw == right_raw.upper()
            and set(left_raw + right_raw) <= _STANDARD_AA_LETTERS
            and (
                (right_raw.startswith(left_raw) and len(right_raw) > len(left_raw))
                or (left_raw.startswith(right_raw) and len(left_raw) > len(right_raw))
            )
        ):
            return f'{left_raw}{pos}{right_raw}'

    if _RE_FS_ANY.match(s) or s_upper.startswith(('FS', 'FRAMESHIFT')):
        return 'fsX'

    m_ins = _RE_HGVS_INS.match(s)
    if m_ins:
        left, pos, inserted = m_ins.groups()
        return f'{left.upper()}{pos}{left.upper()}{inserted.upper()}'

    m_del = _RE_HGVS_DEL.match(s)
    if m_del:
        left, pos, deleted = m_del.groups()
        return f'{left.upper()}{deleted.upper()}{pos}{left.upper()}' if deleted else None

    if m_rw:
        left_u, pos, right_u = (g.upper() for g in m_rw.groups())
        if len(left_u) == 1 and len(right_u) == 1:
            return None if right_u == 'X' else right_u
        if (right_u.startswith(left_u) and len(right_u) > len(left_u)) or (
            left_u.startswith(right_u) and len(left_u) > len(right_u)
        ):
            return f'{left_u}{pos}{right_u}'
        return None

    if _RE_DEL_PREFIX.match(s) or s_upper == 'DEL':
        return None
    if s_upper == 'INS_ANY':
        return 'INS_any'
    if s_upper.startswith('INS'):
        inserted = s[3:].strip().upper()
        if inserted and ref and position_1based is not None:
            return f'{ref}{position_1based}{ref}{inserted}'
        return None
    if _RE_BARE_AA.match(s):
        return None if s_upper == 'X' else s_upper
    return None


def classify_similarity(
    observed_aa: str,
    rule_aa: str,
    high_threshold: int = 1,
    moderate_threshold: int = 0,
) -> str:
    """Classify biochemical similarity between two amino acids via BLOSUM62."""
    try:
        score = _BLOSUM62[observed_aa.upper(), rule_aa.upper()]
    except (KeyError, IndexError):
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
    """Assign an AF bin label to each annotation in place based on its amino-acid frequency."""
    sorted_bins = sorted(bins.items(), key=lambda item: -item[1][0])
    for ann in annotations:
        ann.af_bin = ''
        af = ann.single_exchange_aa_freq if ann.is_combined_codon_event else ann.variant.allele_freq
        for label, (lo, hi) in sorted_bins:
            if lo <= af <= hi:
                ann.af_bin = label
                break
    return annotations
