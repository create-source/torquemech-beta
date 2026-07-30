import re
import unittest
from pathlib import Path


NAV_JS = Path("static/nav.js")
NAV_TEMPLATE = Path("templates/partials/nav.html")


def notification_swipe_config() -> dict[str, float]:
    source = NAV_JS.read_text(encoding="utf-8")
    config = {}
    for name in (
        "notificationSwipeReveal",
        "notificationSwipeThreshold",
        "notificationSwipeDirectionThreshold",
        "notificationSwipeHorizontalRatio",
    ):
        match = re.search(rf"const {name} = ([0-9.]+);", source)
        if not match:
            raise AssertionError(f"{name} constant not found")
        config[name] = float(match.group(1))
    return config


def resolve_swipe(
    moves: list[tuple[float, float]],
    *,
    start_open: bool = False,
    canceled: bool = False,
) -> dict[str, object]:
    config = notification_swipe_config()
    reveal = config["notificationSwipeReveal"]
    threshold = config["notificationSwipeThreshold"]
    direction_threshold = config["notificationSwipeDirectionThreshold"]
    horizontal_ratio = config["notificationSwipeHorizontalRatio"]
    start_offset = -reveal if start_open else 0
    direction = None
    offsets = []
    prevent_default_count = 0

    def direction_for(abs_x: float, abs_y: float) -> str | None:
        if max(abs_x, abs_y) < direction_threshold:
            return None
        if abs_x >= direction_threshold and abs_x >= abs_y * horizontal_ratio:
            return "horizontal"
        return "vertical"

    for delta_x, delta_y in moves:
        abs_x = abs(delta_x)
        abs_y = abs(delta_y)
        if not direction:
            direction = direction_for(abs_x, abs_y)
            if not direction:
                continue
            if direction == "vertical":
                return {
                    "direction": direction,
                    "open": False,
                    "offsets": offsets,
                    "prevent_default_count": prevent_default_count,
                    "dragging": False,
                }

        if direction == "horizontal":
            prevent_default_count += 1
            offsets.append(min(0, max(-reveal, start_offset + delta_x)))

    if canceled:
        return {
            "direction": direction,
            "open": False,
            "offsets": offsets + [0],
            "prevent_default_count": prevent_default_count,
            "dragging": False,
        }

    end_x, end_y = moves[-1]
    if direction == "horizontal":
        is_open = end_x <= -threshold
    else:
        is_open = False

    return {
        "direction": direction,
        "open": is_open,
        "offsets": offsets,
        "prevent_default_count": prevent_default_count,
        "dragging": False,
    }


class NotificationSwipeInteractionTests(unittest.TestCase):
    def test_mostly_vertical_movement_with_small_horizontal_drift_does_not_open(self):
        result = resolve_swipe([(-6, 14), (-10, 44)])

        self.assertEqual(result["direction"], "vertical")
        self.assertFalse(result["open"])
        self.assertEqual(result["offsets"], [])
        self.assertEqual(result["prevent_default_count"], 0)

    def test_diagonal_movement_slightly_more_horizontal_defaults_to_vertical(self):
        result = resolve_swipe([(-12, 10), (-60, 52)])

        self.assertEqual(result["direction"], "vertical")
        self.assertFalse(result["open"])
        self.assertEqual(result["offsets"], [])
        self.assertEqual(result["prevent_default_count"], 0)

    def test_vertical_movement_then_larger_horizontal_movement_remains_vertical(self):
        result = resolve_swipe([(-7, 10), (-90, 36)])

        self.assertEqual(result["direction"], "vertical")
        self.assertFalse(result["open"])
        self.assertEqual(result["offsets"], [])
        self.assertEqual(result["prevent_default_count"], 0)

    def test_short_horizontal_swipe_snaps_closed(self):
        result = resolve_swipe([(-10, 1), (-24, 2)])

        self.assertEqual(result["direction"], "horizontal")
        self.assertFalse(result["open"])
        self.assertGreater(result["prevent_default_count"], 0)

    def test_deliberate_left_swipe_opens_and_clamps_to_action_width(self):
        result = resolve_swipe([(-12, 1), (-120, 5)])
        config = notification_swipe_config()

        self.assertTrue(result["open"])
        self.assertEqual(result["offsets"][-1], -config["notificationSwipeReveal"])

    def test_right_swipe_closes_open_card(self):
        result = resolve_swipe([(14, 1), (56, 2)], start_open=True)

        self.assertEqual(result["direction"], "horizontal")
        self.assertFalse(result["open"])
        self.assertLess(result["offsets"][0], 0)

    def test_touchcancel_resets_dragging_and_snaps_closed(self):
        result = resolve_swipe([(-12, 1), (-40, 2)], canceled=True)

        self.assertEqual(result["direction"], "horizontal")
        self.assertFalse(result["open"])
        self.assertFalse(result["dragging"])
        self.assertEqual(result["offsets"][-1], 0)

    def test_only_horizontal_locked_gestures_call_prevent_default(self):
        vertical = resolve_swipe([(-9, 12), (-70, 54)])
        horizontal = resolve_swipe([(-12, 1), (-60, 4)])

        self.assertEqual(vertical["prevent_default_count"], 0)
        self.assertGreater(horizontal["prevent_default_count"], 0)

    def test_direction_lock_helper_defaults_ambiguous_gestures_to_vertical(self):
        source = NAV_JS.read_text(encoding="utf-8")

        self.assertIn("function notificationSwipeDirection(absX, absY)", source)
        self.assertIn("if (absX >= notificationSwipeDirectionThreshold && absX >= absY * notificationSwipeHorizontalRatio) return \"horizontal\";", source)
        self.assertIn("return \"vertical\";", source)

    def test_opening_one_notification_closes_another(self):
        source = NAV_JS.read_text(encoding="utf-8")

        self.assertIn(
            "if (openNotificationSwipe && openNotificationSwipe !== item) closeNotificationSwipe(openNotificationSwipe);",
            source,
        )
        self.assertIn("openNotificationSwipe = item;", source)

    def test_visible_dismiss_button_posts_to_existing_route(self):
        template = NAV_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('action="/pro/notifications/{{ item.id }}/dismiss"', template)
        self.assertIn('data-notification-dismiss-form', template)
        self.assertIn('button class="tm-notificationItem__dismiss" type="submit"', template)


if __name__ == "__main__":
    unittest.main()
