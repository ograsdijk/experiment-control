from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Union

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.driver import discover_device_members  # noqa: E402


def _member_by_name(device: object, name: str):
    members = discover_device_members(device)
    for member in members:
        if member.name == name:
            return member
    raise AssertionError(f"member {name!r} not found")


class DriverCapabilityDiscoveryTests(unittest.TestCase):
    def test_broad_setter_annotation_uses_runtime_value_annotation(self) -> None:
        class BroadGeneratedPropertyDevice:
            def __init__(self) -> None:
                self.reads = 0

            @property
            def pumpg_statn(self):
                self.reads += 1
                return True

            @pumpg_statn.setter
            def pumpg_statn(self, value: Union[str, int, float]) -> None:
                del value

        device = BroadGeneratedPropertyDevice()
        member = _member_by_name(device, "pumpg_statn")

        self.assertEqual(member.value_annotation, "bool")
        self.assertIsNotNone(member.params)
        assert member.params is not None
        self.assertIn("Union", str(member.params[0].annotation))
        self.assertEqual(device.reads, 1)

    def test_simple_setter_annotation_skips_runtime_property_read(self) -> None:
        class SimpleSetterPropertyDevice:
            def __init__(self) -> None:
                self.reads = 0

            @property
            def threshold(self):
                self.reads += 1
                return 10

            @threshold.setter
            def threshold(self, value: int) -> None:
                del value

        device = SimpleSetterPropertyDevice()
        member = _member_by_name(device, "threshold")

        self.assertIsNone(member.value_annotation)
        self.assertIsNotNone(member.params)
        assert member.params is not None
        self.assertEqual(member.params[0].annotation, "int")
        self.assertEqual(device.reads, 0)

    def test_typed_getter_and_setter_report_annotations_without_runtime_read(self) -> None:
        class TypedPropertyDevice:
            def __init__(self) -> None:
                self.reads = 0

            @property
            def enabled(self) -> bool:
                self.reads += 1
                return True

            @enabled.setter
            def enabled(self, value: bool) -> None:
                del value

        device = TypedPropertyDevice()
        member = _member_by_name(device, "enabled")

        self.assertEqual(member.value_annotation, "bool")
        self.assertIsNotNone(member.params)
        assert member.params is not None
        self.assertEqual(member.params[0].annotation, "bool")
        self.assertEqual(device.reads, 0)


if __name__ == "__main__":
    unittest.main()
