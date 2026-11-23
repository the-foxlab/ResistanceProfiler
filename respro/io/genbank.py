"""GenBank parsing helpers for project initialization."""

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
class ParsedGenBankFeature:
    """One CDS or mat_peptide feature extracted from a GenBank record."""

    reference_name: str
    reference_accession: str
    feature_name: str
    protein: str
    protein_id: str = ''
    locus_tag: str = ''
    note: str = ''
    start: int = 0
    end: int = 0
    strand: str = '+'
    codon_start: int = 0
    nt_sequence: str = ''
    aa_sequence: str = ''
    feature_type: str = 'CDS'
    parent_feature_name: str = ''
    segments: tuple[tuple[int, int], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParsedGenBankReference:
    """One reference sequence and its parsed coding features from GenBank."""

    name: str
    accession: str
    length: int
    organism: str = ''
    taxonomy: str = ''
    features: tuple[ParsedGenBankFeature, ...] = field(default_factory=tuple)


def parse_genbank_sources(genbank_paths: list[Path]) -> list[ParsedGenBankReference]:
    """Parse and combine one or more GenBank files, enforcing globally unique reference IDs."""
    records: list[ParsedGenBankReference] = []
    for path in genbank_paths:
        records.extend(parse_genbank_records(path))
    if not records:
        raise ValueError('No GenBank records found in the provided input file(s)')
    _validate_unique_reference_identifiers(records)
    return records


def parse_genbank_records(genbank_path: Path) -> list[ParsedGenBankReference]:
    """Parse all GenBank records and their CDS/mat_peptide features from one ``.gb`` file."""
    records: list[ParsedGenBankReference] = []
    with genbank_path.open(encoding='utf-8') as fh:
        for record in SeqIO.parse(fh, 'genbank'):
            reference_name = record.id.strip() or record.name.strip()
            sequence = str(record.seq).upper()
            if not reference_name:
                raise ValueError('Encountered a GenBank record without a usable identifier')
            if not sequence:
                raise ValueError(f'GenBank record {reference_name!r} has no sequence')

            accession = _extract_accession(record)
            records.append(ParsedGenBankReference(
                name=reference_name,
                accession=accession,
                length=len(sequence),
                organism=_extract_organism(record),
                taxonomy=_extract_taxonomy(record),
                features=tuple(_parse_coding_features(record, reference_name, accession)),
            ))

    if not records:
        raise ValueError(f'No GenBank records found in {genbank_path}')
    _validate_unique_reference_identifiers(records)
    return records


def validate_strand(strand: str) -> str:
    """Normalize a strand token to ``'+'`` or ``'-'``."""
    if strand in ('+', '1', 'plus', 'forward'):
        return '+'
    if strand in ('-', '-1', 'minus', 'reverse'):
        return '-'
    raise ValueError(f'Invalid strand value: {strand!r}')


def _extract_accession(record: SeqRecord) -> str:
    raw: object = record.annotations.get('accessions', [])
    if isinstance(raw, (list, tuple)) and raw:
        first = str(raw[0]).strip()
        if first:
            return first
    return (record.id or '').strip() or (record.name or '').strip()


def _extract_organism(record: SeqRecord) -> str:
    organism = str(record.annotations.get('organism', '')).strip()
    if organism:
        return organism
    for feature in record.features or []:
        if feature.type == 'source':
            organism = _first_qualifier(feature, 'organism')
            if organism:
                return organism
    return ''


def _extract_taxonomy(record: SeqRecord) -> str:
    raw: object = record.annotations.get('taxonomy', [])
    if isinstance(raw, (list, tuple)) and raw:
        return '; '.join(str(item).strip() for item in raw if str(item).strip())
    return ''


def _validate_feature_location(feature: SeqFeature, *, reference_name: str, feature_name: str) -> None:
    """Validate that a GenBank feature has a concrete, single-strand location."""
    location = feature.location
    if location is None:
        raise ValueError(f'Feature {feature_name!r} in {reference_name!r} has no valid genomic location')
    if isinstance(location, CompoundLocation):
        part_strands = {part.strand for part in location.parts}
        if len(part_strands) > 1:
            raise ValueError(
                f'Feature {feature_name!r} in {reference_name!r} has mixed-strand compound location '
                'which is not supported'
            )


def _parse_codon_start(feature: SeqFeature, *, reference_name: str, feature_name: str) -> int:
    """Parse GenBank 1-based ``/codon_start`` (1, 2, or 3) into a 0-based frame offset."""
    raw = _first_qualifier(feature, 'codon_start', default='1')
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f'Invalid /codon_start {raw!r} for feature {feature_name!r} in {reference_name!r}; expected 1, 2, or 3'
        ) from exc
    if value not in (1, 2, 3):
        raise ValueError(
            f'Invalid /codon_start {value} for feature {feature_name!r} in {reference_name!r}; expected 1, 2, or 3'
        )
    return value - 1


def _build_parsed_feature(
    record: SeqRecord,
    feature: SeqFeature,
    *,
    reference_name: str,
    accession: str,
    feature_name: str,
    feature_type: str,
    parent_feature_name: str = '',
) -> ParsedGenBankFeature:
    """Construct one ``ParsedGenBankFeature`` for either a ``CDS`` or ``mat_peptide``."""
    _validate_feature_location(feature, reference_name=reference_name, feature_name=feature_name)
    assert feature.location is not None
    codon_start = _parse_codon_start(feature, reference_name=reference_name, feature_name=feature_name)
    nt_sequence = str(feature.extract(record.seq)).upper().replace('U', 'T')
    aa_sequence = _translate_feature(
        feature,
        nt_sequence=nt_sequence,
        codon_start=codon_start,
        reference_name=reference_name,
        feature_name=feature_name,
    )
    return ParsedGenBankFeature(
        reference_name=reference_name,
        reference_accession=accession,
        feature_name=feature_name,
        protein=_first_qualifier(feature, 'product', 'protein', default=feature_name),
        protein_id=_first_qualifier(feature, 'protein_id'),
        locus_tag=_first_qualifier(feature, 'locus_tag'),
        note=_first_qualifier(feature, 'note'),
        start=int(feature.location.start),
        end=int(feature.location.end),
        strand='+' if feature.location.strand != -1 else '-',
        codon_start=codon_start,
        nt_sequence=nt_sequence,
        aa_sequence=aa_sequence,
        feature_type=feature_type,
        parent_feature_name=parent_feature_name,
        segments=_extract_segments(feature),
    )


def _parse_coding_features(
    record: SeqRecord,
    reference_name: str,
    accession: str,
) -> list[ParsedGenBankFeature]:
    """Extract CDS features first, then mat_peptide features linked to their enclosing CDS."""
    features: list[ParsedGenBankFeature] = []
    cds_features: list[ParsedGenBankFeature] = []
    seen_names: set[str] = set()

    for feature in record.features or []:
        if feature.type != 'CDS':
            continue
        feature_name = _extract_feature_name(feature, reference_name)
        if feature_name in seen_names:
            logger.warning(
                'Skipping duplicate CDS %r in %r (already loaded; likely present in '
                'both copies of an inverted repeat region).',
                feature_name, reference_name,
            )
            continue
        seen_names.add(feature_name)
        parsed = _build_parsed_feature(
            record, feature,
            reference_name=reference_name, accession=accession,
            feature_name=feature_name, feature_type='CDS',
        )
        cds_features.append(parsed)
        features.append(parsed)

    mat_peptide_index = 0
    for feature in record.features or []:
        if feature.type != 'mat_peptide':
            continue
        mat_peptide_index += 1
        _validate_feature_location(
            feature, reference_name=reference_name, feature_name=f'mat_peptide_{mat_peptide_index}',
        )
        assert feature.location is not None
        strand = '+' if feature.location.strand != -1 else '-'
        parent_name = _find_parent_cds(
            cds_features, int(feature.location.start), int(feature.location.end), strand,
        )
        feature_name = _build_mat_peptide_name(
            feature, parent_feature_name=parent_name, index=mat_peptide_index, seen_names=seen_names,
        )
        seen_names.add(feature_name)
        features.append(_build_parsed_feature(
            record, feature,
            reference_name=reference_name, accession=accession,
            feature_name=feature_name, feature_type='mat_peptide',
            parent_feature_name=parent_name,
        ))

    return features


def _find_parent_cds(
    cds_features: list[ParsedGenBankFeature],
    start: int,
    end: int,
    strand: str,
) -> str:
    for cds in cds_features:
        if cds.strand == strand and start >= cds.start and end <= cds.end:
            return cds.feature_name
    return ''


def _build_mat_peptide_name(
    feature: SeqFeature,
    *,
    parent_feature_name: str,
    index: int,
    seen_names: set[str],
) -> str:
    base = _first_qualifier(feature, 'gene', 'protein_id', 'product')
    if not base:
        base = f'{parent_feature_name}_mat_peptide_{index}' if parent_feature_name else f'mat_peptide_{index}'
    if base not in seen_names:
        return base
    suffix = 2
    while f'{base}_{suffix}' in seen_names:
        suffix += 1
    return f'{base}_{suffix}'


def _extract_segments(feature: SeqFeature) -> tuple[tuple[int, int], ...]:
    """Return 0-based ``[start, end)`` exon segments in 5'->3' coding order."""
    location = feature.location
    assert location is not None
    if isinstance(location, CompoundLocation):
        # Preserve BioPython's location.parts order, which always matches 5'->3' coding
        # concatenation (including complement(join(...)) and circular origin-wrapping CDSs).
        return tuple((int(part.start), int(part.end)) for part in location.parts)
    return ((int(location.start), int(location.end)),)


def _translate_feature(
    feature: SeqFeature,
    *,
    nt_sequence: str,
    codon_start: int,
    reference_name: str,
    feature_name: str,
) -> str:
    """Use the GenBank ``translation`` qualifier when present, else translate ``nt_sequence``."""
    stored = _first_qualifier(feature, 'translation')
    raw = stored if stored else str(Seq(nt_sequence[codon_start:]).translate())
    aa = ''.join(raw.strip().split()).upper()
    if not aa:
        return ''
    if '*' in aa[:-1]:
        raise ValueError(
            f'CDS {feature_name!r} in {reference_name!r} contains internal stop codon(s) '
            'in translation and is considered invalid'
        )
    return aa[:-1] if aa.endswith('*') else aa


def _extract_feature_name(feature: SeqFeature, reference_name: str) -> str:
    name = _first_qualifier(feature, 'gene', 'locus_tag', 'protein_id', 'product', 'protein')
    if not name:
        raise ValueError(
            f'CDS feature in GenBank record {reference_name!r} lacks gene/locus_tag/protein_id/product'
        )
    return name


def _first_qualifier(feature: SeqFeature, *keys: str, default: str = '') -> str:
    for key in keys:
        values = feature.qualifiers.get(key)
        if values:
            value = str(values[0]).strip()
            if value:
                return value
    return default


def _validate_unique_reference_identifiers(records: list[ParsedGenBankReference]) -> None:
    if len({r.name for r in records}) != len(records):
        raise ValueError('GenBank file contains duplicate record identifiers')
    if len({r.accession for r in records}) != len(records):
        raise ValueError('GenBank file contains duplicate accessions across records')
