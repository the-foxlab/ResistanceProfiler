"""
GenBank parsing helpers for project initialisation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedGenBankGene:
    """A CDS/gene extracted from a GenBank record."""

    reference_name: str
    reference_accession: str
    gene_name: str
    protein: str
    protein_id: str = ''
    locus_tag: str = ''
    note: str = ''
    start: int = 0
    end: int = 0
    strand: str = '+'
    codon_start: int = 0  # 0-based offset (GenBank codon_start qualifier minus 1)
    nt_sequence: str = ''  # CDS nucleotide slice in coding orientation
    aa_sequence: str = ''  # pre-translated amino acid sequence (stop codon excluded)
    feature_type: str = 'CDS'
    parent_gene_name: str = ''
    segments: tuple[tuple[int, int], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParsedGenBankReference:
    """A reference sequence and its CDS annotations from GenBank."""

    name: str
    accession: str
    length: int
    organism: str = ''
    taxonomy: str = ''
    genes: tuple[ParsedGenBankGene, ...] = field(default_factory=tuple)


def parse_genbank_sources(genbank_paths: list[Path]) -> list[ParsedGenBankReference]:
    """
    Parse and combine one or more GenBank files.

    Each input file may itself contain one or more GenBank records. The
    returned list is validated globally so duplicate reference identifiers or
    accessions are rejected even when they come from different input files.

    :param genbank_paths: one or more GenBank file paths
    :return: combined list of ParsedGenBankReference objects
    """
    records: list[ParsedGenBankReference] = []

    for genbank_path in genbank_paths:
        records.extend(parse_genbank_records(genbank_path))

    if not records:
        raise ValueError('No GenBank records found in the provided input file(s)')

    _validate_unique_reference_identifiers(records)
    return records


def parse_genbank_records(genbank_path: Path) -> list[ParsedGenBankReference]:
    """
    Parse one or more GenBank records from a file.

    The project stores one internal reference per GenBank record and one gene per
    CDS feature. Gene identifiers are extracted from the GenBank qualifiers and are
    expected to match the rule TSV gene identifiers for the corresponding reference.

    :param genbank_path: path to GenBank file
    :return: list of ParsedGenBankReference objects
    """
    records: list[ParsedGenBankReference] = []

    for record in SeqIO.parse(str(genbank_path), 'genbank'):
        reference_name = record.id.strip() or record.name.strip()
        accession = _extract_accession(record)
        organism = _extract_organism(record)
        taxonomy = _extract_taxonomy(record)
        sequence = str(record.seq).upper()

        if not reference_name:
            raise ValueError('Encountered a GenBank record without a usable identifier')
        if not sequence:
            raise ValueError(f'GenBank record {reference_name!r} has no sequence')

        genes = tuple(_parse_cds_features(record, reference_name, accession))
        records.append(
            ParsedGenBankReference(
                name=reference_name,
                accession=accession,
                length=len(sequence),
                organism=organism,
                taxonomy=taxonomy,
                genes=genes,
            )
        )

    if not records:
        raise ValueError(f'No GenBank records found in {genbank_path}')

    _validate_unique_reference_identifiers(records)
    return records


def validate_strand(strand: str) -> str:
    """
    Normalise a GenBank strand value to '+' or '-'.

    :param strand: raw strand value from a feature record
    :return: '+' or '-'
    :raises ValueError: if the value is not a recognised strand token
    """
    if strand in ('+', '1', 'plus', 'forward'):
        return '+'
    if strand in ('-', '-1', 'minus', 'reverse'):
        return '-'
    raise ValueError(f'Invalid strand value: {strand!r}')


def _extract_accession(record: SeqRecord) -> str:
    """
    Return the preferred accession/identifier for a GenBank record.

    :param record: BioPython SeqRecord object
    :return: accession string
    """
    accessions = record.annotations.get('accessions', [])
    if accessions:
        accession = str(accessions[0]).strip()
        if accession:
            return accession
    return record.id.strip() or record.name.strip()


def _extract_organism(record: SeqRecord) -> str:
    """
    Return the organism/source label for a GenBank record if available.

    :param record: BioPython SeqRecord object
    :return: organism string or empty string
    """
    organism = str(record.annotations.get('organism', '')).strip()
    if organism:
        return organism

    for feature in record.features or []:
        if feature.type != 'source':
            continue
        organism = _first_qualifier(feature, 'organism', default='')
        if organism:
            return organism
    return ''


def _extract_taxonomy(record: SeqRecord) -> str:
    """
    Return semicolon-separated taxonomy if available.

    :param record: BioPython SeqRecord object
    :return: taxonomy string or empty string
    """
    taxonomy = record.annotations.get('taxonomy', [])
    if taxonomy:
        return '; '.join(str(item).strip() for item in taxonomy if str(item).strip())
    return ''


def _parse_cds_features(
    record: SeqRecord,
    reference_name: str,
    accession: str,
) -> list[ParsedGenBankGene]:
    """
    Extract CDS features from a GenBank record and translate each to protein.

    :param record: BioPython SeqRecord object
    :param reference_name: name of the reference sequence
    :param accession: accession identifier
    :return: list of ParsedGenBankGene objects with aa_sequence populated
    """
    genes: list[ParsedGenBankGene] = []
    seen_gene_names: set[str] = set()
    cds_genes: list[ParsedGenBankGene] = []
    mat_peptide_index = 0

    for feature in record.features or []:
        if feature.type != 'CDS':
            continue

        gene_name = _extract_gene_name(feature, reference_name)
        if gene_name in seen_gene_names:
            logger.warning(
                'Skipping duplicate CDS %r in %r (already loaded; likely present in '
                'both copies of an inverted repeat region).',
                gene_name,
                reference_name,
            )
            continue
        seen_gene_names.add(gene_name)

        product = _first_qualifier(feature, 'product', 'protein', default=gene_name)
        protein_id = _first_qualifier(feature, 'protein_id', default='')
        locus_tag = _first_qualifier(feature, 'locus_tag', default='')
        note = _first_qualifier(feature, 'note', default='')
        codon_start = int(_first_qualifier(feature, 'codon_start', default='1')) - 1
        strand = '+' if feature.location.strand != -1 else '-'
        start = int(feature.location.start)
        end = int(feature.location.end)
        segments = _extract_segments(feature)
        nt_sequence = str(feature.extract(record.seq)).upper().replace('U', 'T')
        aa_sequence = _translate_feature(
            feature,
            nt_sequence=nt_sequence,
            codon_start=codon_start,
            reference_name=reference_name,
            gene_name=gene_name,
        )

        cds_gene = ParsedGenBankGene(
            reference_name=reference_name,
            reference_accession=accession,
            gene_name=gene_name,
            protein=product,
            protein_id=protein_id,
            locus_tag=locus_tag,
            note=note,
            start=start,
            end=end,
            strand=strand,
            codon_start=codon_start,
            nt_sequence=nt_sequence,
            aa_sequence=aa_sequence,
            feature_type='CDS',
            parent_gene_name='',
            segments=segments,
        )
        cds_genes.append(cds_gene)
        genes.append(cds_gene)

    for feature in record.features or []:
        if feature.type != 'mat_peptide':
            continue

        mat_peptide_index += 1
        strand = '+' if feature.location.strand != -1 else '-'
        start = int(feature.location.start)
        end = int(feature.location.end)
        parent_gene_name = _find_parent_cds_gene_name(cds_genes, start, end, strand)
        gene_name = _build_mat_peptide_gene_name(
            feature,
            parent_gene_name=parent_gene_name,
            index=mat_peptide_index,
            seen_names=seen_gene_names,
        )
        seen_gene_names.add(gene_name)

        product = _first_qualifier(feature, 'product', 'protein', default=gene_name)
        protein_id = _first_qualifier(feature, 'protein_id', default='')
        locus_tag = _first_qualifier(feature, 'locus_tag', default='')
        note = _first_qualifier(feature, 'note', default='')
        codon_start = int(_first_qualifier(feature, 'codon_start', default='1')) - 1
        segments = _extract_segments(feature)
        nt_sequence = str(feature.extract(record.seq)).upper().replace('U', 'T')
        aa_sequence = _translate_feature(
            feature,
            nt_sequence=nt_sequence,
            codon_start=codon_start,
            reference_name=reference_name,
            gene_name=gene_name,
        )

        genes.append(
            ParsedGenBankGene(
                reference_name=reference_name,
                reference_accession=accession,
                gene_name=gene_name,
                protein=product,
                protein_id=protein_id,
                locus_tag=locus_tag,
                note=note,
                start=start,
                end=end,
                strand=strand,
                codon_start=codon_start,
                nt_sequence=nt_sequence,
                aa_sequence=aa_sequence,
                feature_type='mat_peptide',
                parent_gene_name=parent_gene_name,
                segments=segments,
            )
        )

    return genes


def _find_parent_cds_gene_name(
    cds_genes: list[ParsedGenBankGene],
    start: int,
    end: int,
    strand: str,
) -> str:
    """Return the first CDS gene name that contains the given interval on the same strand."""
    for cds_gene in cds_genes:
        if cds_gene.strand != strand:
            continue
        if start >= cds_gene.start and end <= cds_gene.end:
            return cds_gene.gene_name
    return ''


def _build_mat_peptide_gene_name(
    feature: SeqFeature,
    *,
    parent_gene_name: str,
    index: int,
    seen_names: set[str],
) -> str:
    """Return a unique mat_peptide gene name using the configured fallback order."""
    base_name = _first_qualifier(feature, 'gene', 'protein_id', 'product', default='')
    if not base_name:
        if parent_gene_name:
            base_name = f'{parent_gene_name}_mat_peptide_{index}'
        else:
            base_name = f'mat_peptide_{index}'
    return _deduplicate_gene_name(base_name, seen_names)


def _deduplicate_gene_name(base_name: str, seen_names: set[str]) -> str:
    """Return base_name or a suffixed variant (_2, _3, ...) not present in seen_names."""
    if base_name not in seen_names:
        return base_name

    suffix = 2
    while True:
        candidate = f'{base_name}_{suffix}'
        if candidate not in seen_names:
            return candidate
        suffix += 1


def _extract_segments(feature: SeqFeature) -> tuple[tuple[int, int], ...]:
    """
    Return CDS genomic segments as 0-based [start, end) intervals.

    For contiguous CDS entries this returns one segment matching the gene row.
    For compound CDS entries this returns one segment per location part in the
    GenBank-provided part order.
    """
    location = feature.location
    if isinstance(location, CompoundLocation):
        return tuple((int(part.start), int(part.end)) for part in location.parts)
    return ((int(location.start), int(location.end)),)


def _translate_feature(
    feature: SeqFeature,
    *,
    nt_sequence: str,
    codon_start: int,
    reference_name: str,
    gene_name: str,
) -> str:
    """
    Return the amino acid sequence for a coding feature.

    Uses the pre-computed ``translation`` qualifier from the GenBank file when
    available (quality-controlled by the submitter). Falls back to in-house
    translation only when no qualifier is present.

    :param feature: coding SeqFeature
    :param nt_sequence: CDS nucleotide slice in coding orientation
    :param codon_start: 0-based codon start offset (GenBank codon_start qualifier minus 1)
    :param reference_name: GenBank reference identifier for error context
    :param gene_name: CDS gene name for error context
    :return: amino acid sequence string without stop codon
    """
    stored = _first_qualifier(feature, 'translation')
    if stored:
        return _sanitize_and_validate_translation(stored, reference_name, gene_name)

    translated = str(Seq(nt_sequence[codon_start:]).translate())
    return _sanitize_and_validate_translation(translated, reference_name, gene_name)


def _sanitize_and_validate_translation(
    raw_translation: str, reference_name: str, gene_name: str
) -> str:
    """Normalize translation text and reject CDS entries with internal stop codons."""
    aa = ''.join(raw_translation.strip().split()).upper()
    if not aa:
        return ''

    if '*' in aa[:-1]:
        raise ValueError(
            f'CDS {gene_name!r} in {reference_name!r} contains internal stop codon(s) '
            'in translation and is considered invalid'
        )

    if aa.endswith('*'):
        aa = aa[:-1]
    return aa


def _extract_gene_name(feature: SeqFeature, reference_name: str) -> str:
    """
    Return the identifier used to match rules to this GenBank CDS feature.

    :param feature: SeqFeature object
    :param reference_name: name of the reference for error messages
    :return: gene identifier string
    """
    gene_name = _first_qualifier(
        feature,
        'gene',
        'locus_tag',
        'protein_id',
        'product',
        'protein',
        default='',
    )
    if not gene_name:
        raise ValueError(
            f'CDS feature in GenBank record {reference_name!r} lacks '
            'gene/locus_tag/protein_id/product'
        )
    return gene_name


def _first_qualifier(feature: SeqFeature, *keys: str, default: str = '') -> str:
    """
    Return the first non-empty qualifier value for the given keys.

    :param feature: SeqFeature object
    :param keys: qualifier keys to search in order
    :param default: default value if no qualifier is found
    :return: qualifier value or default
    """
    for key in keys:
        values = feature.qualifiers.get(key)
        if not values:
            continue
        value = str(values[0]).strip()
        if value:
            return value
    return default


def _validate_unique_reference_identifiers(
    records: list[ParsedGenBankReference],
) -> None:
    """
    Validate reference identifiers across a multi-record GenBank file.

    :param records: list of ParsedGenBankReference objects
    """
    names = [record.name for record in records]
    accessions = [record.accession for record in records]

    if len(names) != len(set(names)):
        raise ValueError('GenBank file contains duplicate record identifiers')
    if len(accessions) != len(set(accessions)):
        raise ValueError('GenBank file contains duplicate accessions across records')

