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


def resolve_swipe(moves: list[tuple[float, float]], *, start_open: bool = False) -> dict[str, object]:
    config = notification_swipe_config()
    reveal = config["notificationSwipeReveal"]
    threshold = config["notificationSwipeThreshold"]
    direction_threshold = config["notificationSwipeDirectionThreshold"]
    horizontal_ratio = config["notificationSwipeHorizontalRatio"]
    start_offset = -reveal if start_open else 0
    direction = None
    offsets = []
    prevent_default_count = 0

    for delta_x, delta_y in moves:
        abs_x = abs(delta_x)
        abs_y = abs(delta_y)
        if not direction:
            if max(abs_x, abs_y) < direction_threshold:
                continue
            if abs_x > abs_y * horizontal_ratio:
                direction = "horizontal"
            elif abs_y > abs_x:
                direction = "vertical"
                return {
                    "direction": direction,
                    "open": False,
                    "offsets": offsets,
                    "prevent_default_count": prevent_default_count,
                }
            else:
                continue

        if direction == "horizontal":
            prevent_default_count += 1
            offsets.append(min(0, max(-reveal, start_offset + delta_x)))

    end_x, end_y = moves[-1]
    if direction == "horizontal" and abs(end_x) > abs(end_y) * horizontal_ratio:
        is_open = end_x <= -threshold
    else:
        is_open = False

    return {
        "direction": direction,
        "open": is_open,
        "offsets": offsets,
        "prevent_default_count": prevent_default_count,
    }


class NotificationSwipeInteractionTests(unittest.TestCase):
    def test_vertical_movement_does_not_open_or_prevent_scroll(self):
        result = resolve_swipe([(-6, 14), (-10, 44)])

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
