from __future__ import annotations

import struct
import unittest

from threefingers.evdev_input import (
    ABS_MT_POSITION_X,
    ABS_MT_POSITION_Y,
    ABS_MT_SLOT,
    ABS_MT_TOOL_TYPE,
    ABS_MT_TRACKING_ID,
    EV_ABS,
    EV_SYN,
    Geometry,
    SYN_DROPPED,
    SYN_REPORT,
    TouchpadDevice,
)
from threefingers.models import MT_TOOL_PALM


def event(etype: int, code: int, value: int) -> bytes:
    return struct.pack("llHHi", 0, 0, etype, code, value)


def syn(code: int = SYN_REPORT) -> bytes:
    return event(EV_SYN, code, 0)


def contact(slot: int, tid: int, x: int, y: int, tool: int = 1) -> bytes:
    return b"".join(
        [
            event(EV_ABS, ABS_MT_SLOT, slot),
            event(EV_ABS, ABS_MT_TRACKING_ID, tid),
            event(EV_ABS, ABS_MT_TOOL_TYPE, tool),
            event(EV_ABS, ABS_MT_POSITION_X, x),
            event(EV_ABS, ABS_MT_POSITION_Y, y),
        ]
    )


class TouchpadParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = TouchpadDevice("/dev/null")

    def test_three_contacts_snapshot(self) -> None:
        data = b"".join(
            [
                contact(0, 10, 100, 200),
                contact(1, 11, 140, 210),
                contact(2, 12, 180, 220),
                syn(),
            ]
        )
        reports = self.device.feed(data)
        self.assertEqual(len(reports), 1)
        snapshot = reports[0]
        assert snapshot is not None
        self.assertEqual(set(snapshot), {10, 11, 12})
        self.assertEqual(
            (snapshot[11].x, snapshot[11].y), (140.0, 210.0)
        )

    def test_split_reads_are_buffered(self) -> None:
        data = b"".join(
            [contact(0, 10, 100, 200), syn()]
        )
        self.assertEqual(self.device.feed(data[:7]), [])
        reports = self.device.feed(data[7:])
        self.assertEqual(len(reports), 1)
        assert reports[0] is not None
        self.assertIn(10, reports[0])

    def test_lift_removes_contact(self) -> None:
        first = b"".join([contact(0, 10, 100, 200), syn()])
        self.device.feed(first)
        lift = b"".join(
            [
                event(EV_ABS, ABS_MT_SLOT, 0),
                event(EV_ABS, ABS_MT_TRACKING_ID, -1),
                syn(),
            ]
        )
        reports = self.device.feed(lift)
        self.assertEqual(reports, [{}])

    def test_palm_tool_type_is_preserved(self) -> None:
        data = b"".join(
            [contact(0, 10, 100, 200, tool=MT_TOOL_PALM), syn()]
        )
        reports = self.device.feed(data)
        assert reports[0] is not None
        self.assertEqual(reports[0][10].tool_type, MT_TOOL_PALM)

    def test_syn_dropped_returns_none_and_clears(self) -> None:
        data = b"".join(
            [contact(0, 10, 100, 200), syn(SYN_DROPPED), syn()]
        )
        reports = self.device.feed(data)
        self.assertEqual(reports, [None, {}])

    def test_incomplete_position_is_not_reported(self) -> None:
        data = b"".join(
            [
                event(EV_ABS, ABS_MT_SLOT, 0),
                event(EV_ABS, ABS_MT_TRACKING_ID, 10),
                event(EV_ABS, ABS_MT_POSITION_X, 100),
                syn(),
            ]
        )
        reports = self.device.feed(data)
        self.assertEqual(reports, [{}])

    def test_slot_reuse_gets_new_position(self) -> None:
        self.device.feed(b"".join([contact(0, 10, 100, 200), syn()]))
        # 同一 slot 上换成新的 tracking id
        data = b"".join([contact(0, 11, 300, 400), syn()])
        reports = self.device.feed(data)
        assert reports[0] is not None
        self.assertEqual(set(reports[0]), {11})
        self.assertEqual(reports[0][11].x, 300)


class GeometryTests(unittest.TestCase):
    def test_resolution_used_when_present(self) -> None:
        geometry = Geometry(0, 1000, 0, 500, 40, 40, None, None)
        self.assertAlmostEqual(geometry.units_per_mm_x, 40.0)
        self.assertAlmostEqual(geometry.units_per_mm_y, 40.0)

    def test_udev_size_fallback(self) -> None:
        geometry = Geometry(0, 960, 0, 520, 0, 0, 96.0, 52.0)
        self.assertAlmostEqual(geometry.units_per_mm_x, 10.0)
        self.assertAlmostEqual(geometry.units_per_mm_y, 10.0)

    def test_unknown_size_fallback(self) -> None:
        geometry = Geometry(0, 1000, 0, 500, 0, 0, None, None)
        self.assertAlmostEqual(geometry.units_per_mm_x, 10.0)
        self.assertAlmostEqual(geometry.units_per_mm_y, 500 / 60)


if __name__ == "__main__":
    unittest.main()
