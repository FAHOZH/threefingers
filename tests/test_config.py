from __future__ import annotations

import json
import unittest

from threefingers.config import validate_config
from threefingers.screen import _CONNECTED_RE


class ConfigTests(unittest.TestCase):
    def test_defaults_and_types(self) -> None:
        cfg = validate_config(
            {
                "enabled": 0,
                "sensitivity": "1.7",
                "drag_threshold_mm": 99,
                "invert_x": "yes",
                "device": "  ",
            }
        )
        self.assertIs(cfg["enabled"], False)
        self.assertAlmostEqual(cfg["sensitivity"], 1.7)
        self.assertAlmostEqual(cfg["drag_threshold_mm"], 10.0)
        self.assertIs(cfg["invert_x"], True)
        self.assertEqual(cfg["device"], "auto")

    def test_bad_values_fall_back(self) -> None:
        cfg = validate_config({"sensitivity": "abc"})
        self.assertAlmostEqual(cfg["sensitivity"], 1.0)

    def test_roundtrip(self) -> None:
        # 只验证 JSON 可序列化；路径逻辑由运行环境决定
        cfg = validate_config({"enabled": False, "sensitivity": 1.3})
        encoded = json.dumps(cfg)
        self.assertIn('"sensitivity": 1.3', encoded)

    def test_xrandr_line_regex(self) -> None:
        line = (
            "eDP-1 connected 2560x1440+0+0 "
            "(normal left inverted right x axis y axis) 309mm x 174mm"
        )
        match = _CONNECTED_RE.match(line)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.groups()[1:], ("2560", "1440", "309", "174"))

    def test_xrandr_primary_line(self) -> None:
        line = (
            "DP-1 connected primary 1920x1080+0+0 "
            "(normal left inverted right x axis y axis) 527mm x 296mm"
        )
        match = _CONNECTED_RE.match(line)
        self.assertIsNotNone(match)


if __name__ == "__main__":
    unittest.main()
