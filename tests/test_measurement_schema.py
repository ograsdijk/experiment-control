import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.schemas.measurement import (  # noqa: E402
    measurement_schema_from_json,
    normalize_measurement_note_values,
    normalize_measurement_values,
)


class MeasurementSchemaTests(unittest.TestCase):
    def test_parse_and_normalize_profile_values(self) -> None:
        schema = measurement_schema_from_json(
            {
                "version": 1,
                "profiles": [
                    {
                        "id": "frequency_scan",
                        "label": "Frequency Scan",
                        "fields": [
                            {"key": "measurement_name", "type": "string", "required": True},
                            {"key": "laser.seed1_power_dbm", "type": "number", "required": True},
                            {"key": "steps", "type": "integer", "required": True},
                            {"key": "enabled", "type": "boolean", "required": False, "default": True},
                        ],
                    }
                ],
                "notes": {
                    "fields": [
                        {"key": "author", "type": "string", "required": True},
                        {"key": "kind", "type": "string", "required": True, "options": ["note", "issue"]},
                        {"key": "message", "type": "string", "required": True},
                    ]
                },
            }
        )

        profile, flat, nested = normalize_measurement_values(
            schema,
            profile_id="frequency_scan",
            values={
                "measurement_name": "scan-A",
                "laser": {"seed1_power_dbm": "-5.2"},
                "steps": "30",
            },
        )
        self.assertEqual(profile.profile_id, "frequency_scan")
        self.assertEqual(flat["measurement_name"], "scan-A")
        self.assertAlmostEqual(float(flat["laser.seed1_power_dbm"]), -5.2)
        self.assertEqual(int(flat["steps"]), 30)
        self.assertTrue(bool(flat["enabled"]))
        self.assertEqual(nested["measurement_name"], "scan-A")
        self.assertEqual(int(nested["steps"]), 30)
        self.assertIn("laser", nested)

    def test_normalize_note_values(self) -> None:
        schema = measurement_schema_from_json(
            {
                "version": 1,
                "profiles": [],
                "notes": {
                    "fields": [
                        {
                            "key": "author",
                            "type": "string",
                            "required": True,
                            "options": ["alice", "bob"],
                            "allow_custom": True,
                        },
                        {
                            "key": "kind",
                            "type": "string",
                            "required": True,
                            "options": ["note", "issue"],
                        },
                        {"key": "message", "type": "string", "required": True},
                        {"key": "shot_count", "type": "integer", "required": False},
                    ]
                },
            }
        )
        core, payload = normalize_measurement_note_values(
            schema,
            values={
                "author": "custom-user",
                "kind": "note",
                "message": "beam looked stable",
                "shot_count": "42",
                "extra_info": "abc",
            },
        )
        self.assertEqual(core["author"], "custom-user")
        self.assertEqual(core["kind"], "note")
        self.assertEqual(core["message"], "beam looked stable")
        self.assertEqual(payload["shot_count"], 42)
        self.assertEqual(payload["extra_info"], "abc")

    def test_missing_required_field_raises(self) -> None:
        schema = measurement_schema_from_json(
            {
                "version": 1,
                "profiles": [
                    {
                        "id": "frequency_scan",
                        "fields": [
                            {"key": "measurement_name", "type": "string", "required": True},
                        ],
                    }
                ],
                "notes": {
                    "fields": [
                        {"key": "author", "type": "string", "required": True},
                        {"key": "kind", "type": "string", "required": True},
                        {"key": "message", "type": "string", "required": True},
                    ]
                },
            }
        )
        with self.assertRaises(ValueError):
            _profile, _flat, _nested = normalize_measurement_values(
                schema,
                profile_id="frequency_scan",
                values={},
            )


if __name__ == "__main__":
    unittest.main()
