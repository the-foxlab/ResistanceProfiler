"""
Tests for reference resolution.
"""

import sqlite3
from pathlib import Path

from respro.db.schema import open_project_db
from respro.io.reference import (
    load_genes_for_reference,
)


class TestLoadGenes:
    def test_loads_genes(self, project_db: Path):
        conn = open_project_db(project_db)
        genes = load_genes_for_reference(conn, 1)
        conn.close()

        assert len(genes) == 1
        assert genes[0].name == 'gag'
        assert genes[0].start == 0
        assert genes[0].end == 87


