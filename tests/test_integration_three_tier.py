"""End-to-end integration test for the rank-based phenotype system (T8).

Imports a 3-tier rules sheet (susceptible / low-level resistance / resistant),
configures IC50 + by_phenotype interpretation algorithms, runs a report, and
asserts the lowercased verbatim labels are shown coloured by inferred rank.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest
from conftest import TINY_REF_SEQ, make_profiling_result, write_genbank

from respro.cli.init import init_project
from respro.db.models import (
    AnnotatedVariant,
    ResistanceRule,
    VariantCall,
)
from respro.report.html import render_html


@pytest.fixture()
def tiny_genbank(tmp_path: Path) -> Path:
    gb = tmp_path / 'tiny.gb'
    write_genbank(gb, [
        {
            'id': 'tiny_ref',
            'accession': 'tiny_ref',
            'sequence': TINY_REF_SEQ,
            'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
        }
    ])
    return gb


@pytest.fixture()
def project_db(tmp_path: Path, tiny_genbank: Path) -> Path:
    """Initialize a 3-tier project DB with IC50 + by_phenotype algorithms."""
    rules_tsv = tmp_path / 'rules.tsv'
    rules_tsv.write_text(textwrap.dedent("""\
        feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype\tic50
        gag\ttiny_ref\t2\tK\tE\tDrugR\tresistant\t15.0
        gag\ttiny_ref\t3\tA\tV\tDrugL\tlow-level resistance\t4.0
        gag\ttiny_ref\t4\tF\tL\tDrugS\tsusceptible\t0.5
    """))
    metadata = tmp_path / 'metadata.json'
    metadata.write_text(json.dumps({
        'interpretation_algorithms': [
            {
                'name': 'drug_interpretation',
                'method': 'by_phenotype',
            },
        ],
    }))
    db = tmp_path / 'proj.db'
    init_project(
        db_path=db, name='three_tier', genbank_paths=[tiny_genbank],
        rules_tsv=rules_tsv, metadata_json=metadata, additional_info=False,
    )
    return db


class TestThreeTierIntegration:
    """End-to-end: 3-tier DB → report shows rank-coloured verbatim labels."""

    def test_stored_labels_resolve_to_expected_ranks(self, project_db: Path) -> None:
        conn = sqlite3.connect(str(project_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT d.name AS drug, rr.phenotype AS phenotype '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY d.name'
        ).fetchall()
        conn.close()
        from respro.db.phenotype_ranks import label_to_rank
        ranks = {label_to_rank(row['phenotype']) for row in rows}
        # Ranks inferred: susceptible=1, low-level resistance=3, resistant=5.
        assert ranks == {1, 3, 5}

    def test_stored_labels_are_lowercased_verbatim(self, project_db: Path) -> None:
        conn = sqlite3.connect(str(project_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT d.name AS drug, rr.phenotype AS phenotype '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY d.name'
        ).fetchall()
        conn.close()
        observed = {row['drug']: row['phenotype'] for row in rows}
        assert observed == {
            'drugl': 'low-level resistance',
            'drugr': 'resistant',
            'drugs': 'susceptible',
        }

    def test_report_renders_rank_coloured_labels(self, project_db: Path) -> None:
        conn = sqlite3.connect(str(project_db))
        conn.row_factory = sqlite3.Row
        rules = conn.execute(
            'SELECT rr.id, rr.feature_id, rr.drug_id, d.name AS drug_name, '
            'rr.position, rr.reference, rr.mutation, rr.phenotype, rr.ic50 '
            'FROM resistance_rule rr JOIN drug d ON d.id = rr.drug_id '
            'ORDER BY d.name'
        ).fetchall()
        conn.close()

        annotations = []
        for rule in rules:
            ann = AnnotatedVariant(
                variant=VariantCall(
                    chrom='tiny_ref', pos=rule['position'] * 3 - 1,
                    ref='A', alt='G', allele_freq=0.9, depth=300,
                ),
                feature_name='gag', codon_pos=rule['position'],
                ref_aa=rule['reference'], alt_aa=rule['mutation'],
                consequence='missense', af_bin='high',
                rule_matches=[ResistanceRule(
                    id=rule['id'], feature_name='gag', feature_id=rule['feature_id'],
                    drug_name=rule['drug_name'], drug_id=rule['drug_id'],
                    reference_identifier='tiny_ref',
                    position=rule['position'], reference=rule['reference'],
                    mutation=rule['mutation'], phenotype=rule['phenotype'],
                    ic50=rule['ic50'],
                )],
            )
            annotations.append(ann)

        result = make_profiling_result(
            project_name='three_tier', reference_name='tiny_ref',
            reference_length_nt=len(TINY_REF_SEQ),
            total_variants=3, variants_in_cds=3, resistance_hits=3,
            annotations=annotations,
        )

        project_conn = sqlite3.connect(str(project_db))
        project_conn.row_factory = sqlite3.Row
        html = render_html(
            result, similarity_high=1, similarity_moderate=0,
            project_conn=project_conn,
        )
        project_conn.close()

        # Verbatim lowercased labels appear in the HTML.
        assert 'low-level resistance' in html
        assert 'susceptible' in html
        assert 'resistant' in html
        # Rank-inferred badge classes colour each label.
        assert 'phenotype--low-level' in html
        assert 'phenotype--susceptible' in html
        assert 'phenotype--resistant' in html
