"""
Lightweight dataclass models for in-memory pipeline objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Publication:
    """A deduplicated publication entry linked to one or more resistance rules."""

    id: int
    doi: str
    title: str
    pubmed_id: str
    raw_input: str  # original curator string; preserved as display fallback


@dataclass(frozen=True)
class CoverageGap:
    """A codon stretch that could not be assessed due to N-bases or missing alignment in FASTA mode."""

    gene_name: str
    codon_start: int  # 0-based codon index, inclusive
    codon_end: int    # 0-based codon index, inclusive


@dataclass
class GeneRecord:
    """A gene or CDS annotation loaded from the database."""

    id: int
    reference_id: int
    name: str
    protein: str
    start: int
    end: int
    strand: str
    codon_start: int = 0  # 0-based offset (GenBank codon_start qualifier minus 1)
    nt_sequence: str = ''  # CDS nucleotide slice in coding orientation
    aa_sequence: str = ''  # pre-translated protein sequence stored at init time
    reference_accession: str = ''  # accession of the parent reference (e.g. NC_001806)

    @property
    def length_nt(self) -> int:
        return self.end - self.start


    def contains(self, pos: int) -> bool:
        """
        Return True if pos (0-based) falls within this gene.

        :param pos: 0-based genomic position
        :return: True if position is within gene bounds
        """
        return self.start <= pos < self.end

    def nt_offset(self, pos: int) -> int:
        """
        Return 0-based nucleotide offset within the gene.

        :param pos: 0-based genomic position
        :return: 0-based offset within the gene
        """
        if self.strand == '+':
            return pos - self.start
        return (self.end - 1) - pos

    def codon_index(self, pos: int) -> int:
        """
        Return 0-based codon index for a 0-based genomic position.

        :param pos: 0-based genomic position
        :return: 0-based codon index
        """
        return (self.nt_offset(pos) - self.codon_start) // 3

    def codon_position_in_codon(self, pos: int) -> int:
        """
        Return 0-based position within the codon (0, 1, or 2).

        :param pos: 0-based genomic position
        :return: 0-based position within codon
        """
        return (self.nt_offset(pos) - self.codon_start) % 3


@dataclass
class ResistanceRule:
    """A single resistance rule loaded from the database."""

    id: int
    gene_name: str
    gene_id: int
    drug_name: str
    drug_id: int
    reference_identifier: str
    position: int
    reference: str
    mutation: str
    phenotype: str
    clinical_phenotype: str = 'unknown'
    ic50: str = ''
    fold_ic50: str = ''
    source: str = ''
    pubchem_url: str = ''
    description: str = ''
    comment: str = ''
    publications: list[Publication] = field(default_factory=list)


@dataclass
class ResistanceRuleSetMember:
    """One member mutation within a combined resistance rule set."""

    id: int
    rule_set_id: int
    gene_name: str
    gene_id: int
    reference_identifier: str
    position: int
    reference: str
    mutation: str


@dataclass
class ResistanceRuleSet:
    """A combined resistance rule requiring multiple member mutations to co-occur."""

    id: int
    drug_name: str
    drug_id: int
    phenotype: str
    clinical_phenotype: str = 'unknown'
    ic50: str = ''
    fold_ic50: str = ''
    source: str = ''
    group_name: str = ''
    pubchem_url: str = ''
    description: str = ''
    comment: str = ''
    publications: list[Publication] = field(default_factory=list)
    members: list[ResistanceRuleSetMember] = field(default_factory=list)


@dataclass
class VariantCall:
    """A single variant extracted from a VCF record (0-based internal position)."""

    chrom: str
    pos: int
    ref: str
    alt: str
    allele_freq: float = 0.0
    depth: int = 0
    filter_status: str = 'PASS'
    query_ref_codon: str = ''


@dataclass
class AnnotatedVariant:
    """A variant with codon-aware amino acid annotation and rule matches."""

    variant: VariantCall
    gene_name: str = ''
    codon_pos: int = 0
    ref_codon: str = ''
    alt_codon: str = ''
    ref_aa: str = ''
    alt_aa: str = ''
    consequence: str = ''
    is_combined_codon_event: bool = False
    combined_member_count: int = 1
    af_bin: str = ''
    is_fasta_mode: bool = False  # True when derived from consensus FASTA, not a VCF
    rule_matches: list[ResistanceRule] = field(default_factory=list)

    @property
    def is_resistance_hit(self) -> bool:
        return bool(self.rule_matches)

    def drug_hits_json(self) -> list[dict]:
        """
        Return a JSON-serializable list of matched drug/rule info.

        :return: list of dicts containing drug and rule information
        """
        return [
            {
                'drug': r.drug_name,
                'reference_identifier': r.reference_identifier,
                'reference': r.reference,
                'mutation': r.mutation,
                'phenotype': r.phenotype,
                'clinical_phenotype': r.clinical_phenotype,
                'ic50': r.ic50,
                'fold_ic50': r.fold_ic50,
                'publications': [
                    {'doi': p.doi, 'title': p.title, 'pubmed_id': p.pubmed_id, 'raw_input': p.raw_input}
                    for p in r.publications
                ],
                'pubchem_url': r.pubchem_url,
            }
            for r in self.rule_matches
        ]


@dataclass
class ComboRuleHit:
    """A fired combination resistance rule set with its contributing annotated variants."""

    rule_set: ResistanceRuleSet
    matched_variants: list[AnnotatedVariant] = field(default_factory=list)

    def to_dict(self) -> dict:
        """
        Return a JSON-serializable representation of this combo rule hit.

        :return: dict with rule set metadata and matched variant summaries
        """
        rs = self.rule_set
        return {
            'drug': rs.drug_name,
            'phenotype': rs.phenotype,
            'clinical_phenotype': rs.clinical_phenotype,
            'ic50': rs.ic50,
            'fold_ic50': rs.fold_ic50,
            'publications': [
                {'doi': p.doi, 'title': p.title, 'pubmed_id': p.pubmed_id, 'raw_input': p.raw_input}
                for p in rs.publications
            ],
            'source': rs.source,
            'pubchem_url': rs.pubchem_url,
            'rule_group': rs.group_name,
            'members': [
                {
                    'gene': m.gene_name,
                    'position': m.position + 1,  # 1-based for output
                    'reference': m.reference,
                    'mutation': m.mutation,
                }
                for m in rs.members
            ],
            'matched_variants': [
                {
                    'gene': v.gene_name,
                    'codon_pos': v.codon_pos + 1,
                    'ref_aa': v.ref_aa,
                    'alt_aa': v.alt_aa,
                    'allele_freq': v.variant.allele_freq,
                }
                for v in self.matched_variants
            ],
        }

