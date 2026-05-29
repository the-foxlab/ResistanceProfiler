"""
Test fixtures and shared helpers.
"""

from __future__ import annotations

import sqlite3
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from respro.db.models import FeatureRecord
from respro.db.schema import create_schema

# ─── Paths ────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    """
    Provide a clean temporary directory.

    :param tmp_path: pytest-provided temporary directory
    :return: Path to temporary directory
    """
    return tmp_path


# ─── Minimal reference data ──────────────────────────────────────────

# A tiny 90-nt "genome" with one 30-codon feature at positions 1–90
TINY_REF_SEQ = 'ATGAAAGCTTTTGGCCCCAAATTTGGGCCCAAAGCTTTTGGCCCCAAATTTGGGCCCAAAGCTTTTGGCCCCAAATTTGGGCCCAAATAA'
# len = 87 nt  (29 codons → 28 AA + stop)
# Codons: ATG AAA GCT TTT GGC CCC AAA TTT GGG CCC AAA GCT TTT GGC CCC AAA TTT GGG CCC AAA GCT TTT GGC CCC AAA TTT GGG CCC AAA TAA

TINY_REF_NAME = 'tiny_ref'


@pytest.fixture()
def tiny_ref_seq() -> str:
    return TINY_REF_SEQ


@pytest.fixture()
def tiny_feature() -> FeatureRecord:
    """A feature spanning the entire tiny reference."""
    return FeatureRecord(
        id=1,
        reference_id=1,
        name='gag',
        protein='Gag',
        start=0,
        end=87,
        strand='+',
        codon_start=0,
        nt_sequence=TINY_REF_SEQ,
    )


# ─── Project database ────────────────────────────────────────────────

@pytest.fixture()
def project_db(tmp_path: Path) -> Path:
    """Create a minimal project database with one reference, one feature, and rules."""
    db_path = tmp_path / 'test_project.db'
    conn = create_schema(db_path)
    conn.row_factory = sqlite3.Row

    # Project
    conn.execute(
        'INSERT INTO project (name, schema_version, uuid) VALUES (?, ?, ?)',
        ('Test Project', 6, str(uuid.uuid4())),
    )
    # Reference
    conn.execute(
        'INSERT INTO reference (project_id, name, length) VALUES (?, ?, ?)',
        (1, TINY_REF_NAME, len(TINY_REF_SEQ)),
    )
    # feature
    conn.execute(
        'INSERT INTO feature (reference_id, name, protein, start, end, strand, nt_sequence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, 'gag', 'Gag', 0, 87, '+', TINY_REF_SEQ),
    )
    # Drug
    conn.execute(
        'INSERT INTO drug (project_id, name) VALUES (?, ?)',
        (1, 'TestDrug'),
    )
    # Rule: position 1 (0-based = 2nd AA = K), mutation=E -> resistance
    conn.execute(
        'INSERT INTO resistance_rule (feature_id, drug_id, position, reference, mutation, phenotype) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, 1, 1, 'K', 'E', 'resistant'),
    )

    conn.commit()
    conn.close()
    return db_path


# ─── VCF helpers ─────────────────────────────────────────────────────

@pytest.fixture()
def sample_vcf(tmp_path: Path) -> Path:
    """Write a minimal VCF with two variants."""
    vcf_content = textwrap.dedent("""\
        ##fileformat=VCFv4.2
        ##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">
        ##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">
        #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
        tiny_ref\t4\t.\tA\tG\t100\tPASS\tAF=0.95;DP=500
        tiny_ref\t10\t.\tT\tC\t80\tPASS\tAF=0.30;DP=200
    """)
    vcf_path = tmp_path / 'sample.vcf'
    vcf_path.write_text(vcf_content)
    return vcf_path


@pytest.fixture()
def sample_ref_fasta(tmp_path: Path) -> Path:
    """Write the reference FASTA corresponding to the sample VCF."""
    fasta_path = tmp_path / 'sample_ref.fasta'
    fasta_path.write_text(f'>{TINY_REF_NAME}\n{TINY_REF_SEQ}\n')
    return fasta_path


def write_genbank(
    path: Path,
    records: list[dict],
) -> Path:
    """Write one or more minimal GenBank records for tests."""
    seq_records: list[SeqRecord] = []

    for record_data in records:
        record = SeqRecord(
            Seq(record_data['sequence']),
            id=record_data['id'],
            name=record_data.get('name', record_data['id']),
            description=record_data.get('description', ''),
        )
        record.annotations['molecule_type'] = 'DNA'
        record.annotations['accessions'] = [record_data.get('accession', record_data['id'])]
        if 'organism' in record_data:
            record.annotations['organism'] = record_data['organism']
        if 'taxonomy' in record_data:
            record.annotations['taxonomy'] = record_data['taxonomy']
        record.features = []

        for feature in record_data.get('features', []):
            product_value = feature.get('product', feature.get('protein', feature.get('feature', '')))
            qualifiers = {
                'codon_start': [str(feature.get('codon_start', 1))],
            }
            if feature.get('feature'):
                qualifiers['gene'] = [feature['feature']]
            if product_value:
                qualifiers['product'] = [product_value]
            if feature.get('protein_id'):
                qualifiers['protein_id'] = [feature['protein_id']]
            if feature.get('locus_tag'):
                qualifiers['locus_tag'] = [feature['locus_tag']]
            if 'translation' in feature:
                qualifiers['translation'] = [feature['translation']]
            feature = SeqFeature(
                FeatureLocation(
                    feature['start'] - 1,
                    feature['end'],
                    strand=1 if feature.get('strand', '+') == '+' else -1,
                ),
                type='CDS',
                qualifiers=qualifiers,
            )
            record.features.append(feature)

        seq_records.append(record)

    with open(path, 'w') as handle:
        SeqIO.write(seq_records, handle, 'genbank')
    return path


