from __future__ import annotations

import unittest

from threefingers.gesture import (
    ARMED,
    DRAGGING,
    IDLE,
    GestureEngine,
    MotionSettings,
)
from threefingers.models import Contact, MT_TOOL_PALM


class FakeBackend:
    def __init__(self) -> None:
        self.events: list = []
        self.fail_down = False

    def button_down(self) -> None:
        if self.fail_down:
            raise RuntimeError("boom")
        self.events.append(("down",))

    def button_up(self) -> None:
        self.events.append(("up",))

    def move(self, dx: int, dy: int) -> None:
        self.events.append(("move", dx, dy))


def make_settings(**overrides) -> MotionSettings:
    values = dict(
        units_per_mm_x=40.0,
        units_per_mm_y=40.0,
        px_per_mm_x=8.0,
        px_per_mm_y=8.0,
        sensitivity=1.0,
        threshold_mm=1.0,
        invert_x=False,
        invert_y=False,
    )
    values.update(overrides)
    return MotionSettings(**values)


def make_contacts(*items) -> dict[int, Contact]:
    return {
        tid: Contact(tracking_id=tid, x=x, y=y, tool_type=tool_type)
        for tid, x, y, tool_type in items
    }


def three(x=100.0, y=100.0, gap=40.0) -> dict[int, Contact]:
    return make_contacts(
        (1, x, y, 1),
        (2, x + gap, y, 1),
        (3, x + 2 * gap, y, 1),
    )


class GestureEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.engine = GestureEngine(self.backend, make_settings())

    def test_three_fingers_arm_but_do_not_click(self) -> None:
        self.engine.handle_report(three())
        self.assertEqual(self.engine.state, ARMED)
        self.assertEqual(self.backend.events, [])

    def test_tap_does_not_click(self) -> None:
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=110))  # 0.25mm
        self.engine.handle_report({})
        self.assertEqual(self.engine.state, IDLE)
        self.assertEqual(self.backend.events, [])

    def test_drag_after_threshold_and_release(self) -> None:
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=120))  # 0.5mm, below threshold
        self.assertEqual(self.backend.events, [])
        self.engine.handle_report(three(x=145))  # 1.125mm, crosses threshold
        self.assertEqual(self.engine.state, DRAGGING)
        self.assertEqual(self.backend.events, [("down",)])

        # 40 units = 1mm -> 8 px
        self.engine.handle_report(three(x=185))
        self.assertEqual(self.backend.events[-1], ("move", 8, 0))

        # 抬一根手指结束拖拽
        self.engine.handle_report(make_contacts((1, 225, 100, 1), (2, 265, 100, 1)))
        self.assertEqual(self.engine.state, IDLE)
        self.assertEqual(self.backend.events[-1], ("up",))

    def test_two_or_four_fingers_do_nothing(self) -> None:
        self.engine.handle_report(
            make_contacts((1, 100, 100, 1), (2, 140, 100, 1))
        )
        self.assertEqual(self.engine.state, IDLE)
        self.backend.events.clear()

        four = make_contacts(
            (1, 100, 100, 1),
            (2, 140, 100, 1),
            (3, 180, 100, 1),
            (4, 220, 100, 1),
        )
        self.engine.handle_report(four)
        self.assertEqual(self.engine.state, IDLE)
        self.engine.handle_report(four)
        self.assertEqual(self.backend.events, [])

    def test_fourth_finger_is_ignored_during_drag(self) -> None:
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=145))
        self.engine.handle_report(
            make_contacts(
                (1, 145, 100, 1),
                (2, 185, 100, 1),
                (3, 225, 100, 1),
                (9, 20, 20, 1),  # 第四根手指，位置很远
            )
        )
        # 第四根手指不参与质心计算，原始三指未移动，因此不应产生 move
        self.assertEqual(self.engine.state, DRAGGING)
        self.assertEqual(self.backend.events[-1], ("down",))
        # 再移动原始三指：只按原始三指计算，得到 8px
        self.engine.handle_report(
            make_contacts(
                (1, 185, 100, 1),
                (2, 225, 100, 1),
                (3, 265, 100, 1),
                (9, 20, 20, 1),
            )
        )
        self.assertEqual(self.backend.events[-1], ("move", 8, 0))

    def test_palm_is_ignored(self) -> None:
        contacts = make_contacts(
            (1, 100, 100, 1),
            (2, 140, 100, 1),
            (3, 180, 100, MT_TOOL_PALM),
        )
        self.engine.handle_report(contacts)
        self.assertEqual(self.engine.state, IDLE)
        contacts = make_contacts(
            (1, 100, 100, 1),
            (2, 140, 100, 1),
            (3, 180, 100, MT_TOOL_PALM),
            (4, 220, 100, MT_TOOL_PALM),
        )
        self.engine.handle_report(contacts)
        self.assertEqual(self.engine.state, IDLE)
        self.assertEqual(self.backend.events, [])

    def test_disable_releases_button(self) -> None:
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=145))
        self.engine.set_enabled(False)
        self.assertEqual(self.backend.events[-1], ("up",))
        self.assertEqual(self.engine.state, IDLE)
        self.engine.handle_report(three(x=300))
        self.assertEqual(self.engine.state, IDLE)

    def test_enabling_again_while_dragging_releases(self) -> None:
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=145))
        self.engine.set_enabled(True)  # 例如修改设置时重复设置
        self.assertEqual(self.backend.events[-1], ("up",))
        self.assertEqual(self.engine.state, IDLE)
        self.assertFalse(self.engine.pressed)

    def test_sensitivity_and_inversion(self) -> None:
        engine = GestureEngine(
            self.backend,
            make_settings(sensitivity=2.0, invert_y=True),
        )
        engine.handle_report(three())
        engine.handle_report(three(x=145))
        engine.handle_report(three(x=145, y=140))
        self.assertIn(("move", 0, -16), self.backend.events)

    def test_jump_cancels_drag(self) -> None:
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=145))
        self.engine.handle_report(three(x=3000))  # > 40mm
        self.assertEqual(self.engine.state, IDLE)
        self.assertEqual(self.backend.events[-1], ("up",))

    def test_backend_failure_sets_fatal_error(self) -> None:
        self.backend.fail_down = True
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=145))
        self.assertIsNotNone(self.engine.fatal_error)
        self.assertEqual(self.engine.state, IDLE)

    def test_new_three_finger_gesture_after_release(self) -> None:
        self.engine.handle_report(three())
        self.engine.handle_report(three(x=145))
        self.engine.handle_report({})
        self.assertEqual(self.engine.state, IDLE)
        self.engine.handle_report(three(x=500, y=500))
        self.assertEqual(self.engine.state, ARMED)
        self.engine.handle_report(three(x=545, y=500))
        self.assertEqual(self.engine.state, DRAGGING)

    def test_fractional_accumulation(self) -> None:
        engine = GestureEngine(
            self.backend,
            make_settings(px_per_mm_x=0.5, px_per_mm_y=0.5),
        )
        engine.handle_report(three())
        engine.handle_report(three(x=145))
        self.backend.events.clear()
        for step in range(1, 4):
            engine.handle_report(three(x=145 + 40 * step))
        # 每次 1mm -> 0.5px；累计到第 3 次才应输出 1px
        self.assertEqual(self.backend.events, [("move", 1, 0)])


if __name__ == "__main__":
    unittest.main()
