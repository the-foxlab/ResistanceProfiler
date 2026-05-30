"""
Tests for reference resolution.
"""

from pathlib import Path

from respro.db.features import load_features_for_reference
from respro.db.schema import open_project_db


class TestLoadFeatures:
    def test_loads_features(self, project_db: Path):
        conn = open_project_db(project_db)
        features = load_features_for_reference(conn, 1)
        conn.close()

        assert len(features) == 1
        assert features[0].name == 'gag'
        assert features[0].start == 0
        assert features[0].end == 87

