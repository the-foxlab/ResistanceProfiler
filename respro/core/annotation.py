"""
Codon-aware variant annotation, coordinate helpers, and allele-frequency binning.
"""

from __future__ import annotations

import logging
import re

from Bio.Seq import Seq

from respro.db.models import AnnotatedVariant, GeneRecord, VariantCall

logger = logging.getLogger(__name__)


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
        return aa if aa else '?'
    except Exception:
        return '?'


def annotate_variants(
    variants: list[VariantCall],
    genes: list[GeneRecord],
) -> list[AnnotatedVariant]:
    """
    Annotate a list of variants with codon-aware amino acid consequences.

    Handles SNPs, insertions, deletions, and frameshifts. Variants outside
    any CDS are included with empty gene_name.

    SNP consequences are derived from the internal CDS sequence stored for
    each gene. This keeps annotation and rule matching anchored to the same
    curated reference.

    :param variants: parsed variant calls (0-based positions)
    :param genes: gene annotations for the reference
    :return: list of AnnotatedVariant, one per variant; variants outside any
        gene are included with empty gene_name
    """
    results: list[AnnotatedVariant] = []

    for var in variants:
        matching_genes = [g for g in genes if g.contains(var.pos)]

        if not matching_genes:
            results.append(AnnotatedVariant(variant=var))
            continue

        for gene in matching_genes:
            ann = _annotate_variant_in_gene(var, gene)
            results.append(ann)

    logger.info(
        'Annotated %d variant(s) -> %d annotation(s) (%d in CDS)',
        len(variants),
        len(results),
        sum(1 for a in results if a.gene_name),
    )
    return results


def _variant_type(ref: str, alt: str) -> str:
    """Classify a variant as SNP, INS, or DEL based on allele lengths."""
    if len(ref) == 1 and len(alt) == 1:
        return 'SNP'
    if len(alt) > len(ref):
        return 'INS'
    return 'DEL'


def _annotate_variant_in_gene(
    var: VariantCall,
    gene: GeneRecord,
) -> AnnotatedVariant:
    """
    Annotate a single variant (SNP, insertion, or deletion) within a gene.

    Uses the gene's stored CDS nucleotide sequence in coding orientation
    to split into codons, apply the variant, and determine the amino acid
    consequence.
    """

    seq_cds = gene.nt_sequence.upper()
    if not seq_cds:
        return AnnotatedVariant(variant=var, gene_name=gene.name)

    # Convert genomic variant to CDS coordinates
    if gene.strand == '-':
        cds_variant_pos = (gene.end - 1) - var.pos
        ref = reverse_complement(var.ref)
        mut = reverse_complement(var.alt)
    else:
        cds_variant_pos = var.pos - gene.start
        ref = var.ref
        mut = var.alt

    # Split CDS into codon triplets
    cds_codons = [list(seq_cds[i:i + 3]) for i in range(0, len(seq_cds), 3)]
    mut_codon_idx = cds_variant_pos // 3
    codon_pos_in_codon = cds_variant_pos % 3

    if mut_codon_idx < 0 or mut_codon_idx >= len(cds_codons):
        return AnnotatedVariant(variant=var, gene_name=gene.name)

    vtype = _variant_type(var.ref, var.alt)

    if vtype == 'SNP':
        return _annotate_snp(var, gene, cds_codons, mut_codon_idx, codon_pos_in_codon, mut)
    elif vtype == 'INS':
        return _annotate_insertion(var, gene, cds_codons, mut_codon_idx, codon_pos_in_codon, ref, mut)
    else:
        return _annotate_deletion(var, gene, cds_codons, mut_codon_idx, codon_pos_in_codon, ref, mut)


def _annotate_snp(
    var: VariantCall,
    gene: GeneRecord,
    cds_codons: list[list[str]],
    mut_codon_idx: int,
    codon_pos: int,
    mut: str,
) -> AnnotatedVariant:
    """
    Annotate a single nucleotide substitution.

    When the variant carries a user-provided reference codon (from FASTA
    remapping), the alt amino acid is computed by mutating that codon.
    The ref amino acid always comes from the internal CDS — this is the
    baseline that resistance rules are defined against.  This prevents
    false calls when the user reference has a silent nucleotide difference
    that, combined with the new mutation, changes the translated amino acid.
    """
    affected_codon = ''.join(cds_codons[mut_codon_idx])
    ref_aa = translate_codon(affected_codon)

    alt_codon_bases = list(affected_codon)
    alt_codon_bases[codon_pos] = mut
    alt_codon_str = ''.join(alt_codon_bases)
    alt_aa = translate_codon(alt_codon_str)

    consequence = _classify_snp_consequence(ref_aa, alt_aa, mut_codon_idx)

    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=mut_codon_idx,
        ref_codon=affected_codon,
        alt_codon=alt_codon_str,
        ref_aa=ref_aa,
        alt_aa=alt_aa,
        consequence=consequence,
    )


def _annotate_insertion(
    var: VariantCall,
    gene: GeneRecord,
    cds_codons: list[list[str]],
    mut_codon_idx: int,
    codon_pos: int,
    ref: str,
    mut: str,
) -> AnnotatedVariant:
    """Annotate an insertion variant."""
    ref_codon_str = ''.join(cds_codons[mut_codon_idx])
    ref_aa = translate_codon(ref_codon_str)
    inserted_bases = mut[len(ref):]

    # Non-triplet insertion → frameshift
    if len(inserted_bases) % 3 != 0:
        return AnnotatedVariant(
            variant=var,
            gene_name=gene.name,
            codon_pos=mut_codon_idx,
            ref_codon=ref_codon_str,
            alt_codon='',
            ref_aa=ref_aa,
            alt_aa='fsX',
            consequence='frameshift',
        )

    # In-frame insertion: splice inserted bases into the codon context and translate
    alt_codon_bases = list(cds_codons[mut_codon_idx])
    alt_codon_bases[codon_pos] = mut
    expanded_seq = ''.join(alt_codon_bases)
    alt_aa_seq = str(Seq(expanded_seq).translate())

    consequence = 'inframe_insertion'
    if mut_codon_idx == 0 and (not alt_aa_seq or alt_aa_seq[0] != 'M'):
        consequence = 'start_lost'
    elif ref_aa != '*' and '*' in alt_aa_seq[1:]:
        consequence = 'nonsense'
    elif ref_aa == '*' and '*' not in alt_aa_seq:
        consequence = 'stop_loss'

    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=mut_codon_idx,
        ref_codon=ref_codon_str,
        alt_codon=expanded_seq,
        ref_aa=ref_aa,
        alt_aa=alt_aa_seq,
        consequence=consequence,
    )


def _annotate_deletion(
    var: VariantCall,
    gene: GeneRecord,
    cds_codons: list[list[str]],
    mut_codon_idx: int,
    codon_pos: int,
    ref: str,
    mut: str,
) -> AnnotatedVariant:
    """Annotate a deletion variant."""
    ref_codon_str = ''.join(cds_codons[mut_codon_idx])
    ref_aa = translate_codon(ref_codon_str)
    deleted_bases = len(ref) - len(mut)

    # Non-triplet deletion → frameshift
    if deleted_bases % 3 != 0:
        return AnnotatedVariant(
            variant=var,
            gene_name=gene.name,
            codon_pos=mut_codon_idx,
            ref_codon=ref_codon_str,
            alt_codon='',
            ref_aa=ref_aa,
            alt_aa='fsX',
            consequence='frameshift',
        )

    # In-frame deletion: determine affected codons and compute resulting AA
    deleted_codons = deleted_bases // 3
    if codon_pos == 2:
        # Deletion starts at the last position of this codon → next codons are deleted
        affected = cds_codons[mut_codon_idx:mut_codon_idx + 1 + deleted_codons]
        deletion_seq = ''.join(sum(affected, []))
        deletion_aa = str(Seq(deletion_seq).translate())
        new_aa = ref_aa
    else:
        end_idx = mut_codon_idx + 1 + deleted_codons
        affected = cds_codons[mut_codon_idx:end_idx]
        deletion_seq = ''.join(sum(affected, []))
        deletion_aa = str(Seq(deletion_seq).translate())
        # New codon is the kept prefix of the first codon + kept suffix of the last codon
        remaining = affected[0][:codon_pos + 1] + affected[-1][codon_pos + 1:]
        new_aa = str(Seq(''.join(remaining)).translate()) if len(remaining) == 3 else '?'

    consequence = 'inframe_deletion'
    if mut_codon_idx == 0 and new_aa != 'M':
        consequence = 'start_lost'
    elif '*' in deletion_aa and new_aa != '*':
        consequence = 'stop_loss'

    return AnnotatedVariant(
        variant=var,
        gene_name=gene.name,
        codon_pos=mut_codon_idx,
        ref_codon=ref_codon_str,
        alt_codon=''.join(sum(cds_codons[mut_codon_idx:mut_codon_idx + 1], [])),
        ref_aa=deletion_aa,
        alt_aa=new_aa,
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


def normalize_mutation(
    raw: str,
    *,
    reference: str = '',
    position_1based: int | None = None,
) -> str | None:
    """
    Normalise a raw mutation token from a rules TSV to the canonical DB form.

    The canonical tokens are:

    - ``A``–``Z``  — specific alt amino acid (missense, synonymous, stop-loss target)
    - ``*``        — stop gained (nonsense)
    - ``fsX``      — frameshift at this codon
    - ``F50FGG``   — insertion: insertion after F50 resulting in ``FGG``
    - ``FGG50F``   — deletion: deletion from ``FGG`` to ``F`` at anchor position 50
    - ``any``      — wildcard: matches any non-reference amino acid at this position

    The function accepts full notation and common flexible input forms used in
    resistance tables. It normalizes to the project notation above.

    Bare ``x`` / ``X`` are treated as wildcards (``any``).  Bare ``*`` means
    stop-gained and already follows the canonical nomenclature.

    :param raw: raw string from the mutation column of a rules TSV
    :param reference: optional reference AA from the rules row
    :param position_1based: optional 1-based AA position from the rules row
    :return: canonical token, or None if the input cannot be recognised
    """

    # ── Compiled patterns for normalize_mutation ──────────────────────────────────
    _RE_FS_ANY = re.compile(r'^(?:[A-Z*]\d+)?(?:fs|frameshift)', re.IGNORECASE)
    _RE_STOP_FULL = re.compile(r'^[A-Z*]\d+stop$', re.IGNORECASE)
    _RE_REWRITE = re.compile(r'^([A-Z*]+)(\d+)([A-Z*]+)$', re.IGNORECASE)
    _RE_HGVS_INS = re.compile(r'^([A-Z*])(\d+)(?:_[A-Z*]\d+)?ins([A-Z*]+)$', re.IGNORECASE)
    _RE_HGVS_DEL = re.compile(r'^([A-Z*])(\d+)(?:_[A-Z*]\d+)?del([A-Z*]+)?$', re.IGNORECASE)
    _RE_DEL_PREFIX = re.compile(r'^del([A-Z*]+)?(?:[A-Z*]\d+|\d+)$', re.IGNORECASE)
    _RE_BARE_AA = re.compile(r'^[A-Z]$', re.IGNORECASE)

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

    # Wildcard
    if s_upper == 'ANY':
        return 'any'

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

    # Single amino acid letter — bare 'X'/'x' is treated as wildcard
    if _RE_BARE_AA.match(s):
        alt = s_upper
        return 'any' if alt == 'X' else alt

    return None


# ──────────────────────────────────────────────────────────────────────
# Allele-frequency binning
# ──────────────────────────────────────────────────────────────────────

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
        ann.af_bin = _classify_af(af, sorted_bins)

    return annotations


def _classify_af(af: float, sorted_bins: list[tuple[str, tuple[float, float]]]) -> str:
    """
    Return the bin label for a given allele frequency.

    :param af: allele frequency value
    :param sorted_bins: sorted list of (label, (lo, hi)) tuples
    :return: bin label for the allele frequency
    """
    for label, (lo, hi) in sorted_bins:
        if lo <= af <= hi:
            return label
    return 'unknown'
