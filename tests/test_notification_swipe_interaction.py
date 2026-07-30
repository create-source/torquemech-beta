import re
import unittest
from pathlib import Path


NAV_JS = Path("static/nav.js")
NAV_CSS = Path("static/style.css")
NAV_TEMPLATE = Path("templates/partials/nav.html")


class NotificationDismissInteractionTests(unittest.TestCase):
    def test_trash_dismiss_button_posts_to_existing_route_with_csrf(self):
        template = NAV_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn('class="tm-notificationItem__dismissForm"', template)
        self.assertIn('method="post" action="/pro/notifications/{{ item.id }}/dismiss"', template)
        self.assertIn('name="csrf_token" value="{{ request.session.get(\'csrf_token\', \'\') }}"', template)
        self.assertIn('class="tm-notificationItem__trashAction" type="submit" aria-label="Dismiss notification"', template)
        self.assertNotIn("tm-notificationItem__swipeDismiss", template)
        self.assertNotRegex(template, r"tm-notificationItem__dismiss(?!Form)")

    def test_notification_workflow_actions_and_content_stay_visible(self):
        template = NAV_TEMPLATE.read_text(encoding="utf-8")
        css = NAV_CSS.read_text(encoding="utf-8")

        self.assertIn(">Open Finding</button>", template)
        self.assertIn("item.handoff_state.primary_action_label", template)
        self.assertIn("{{ item.created_at_display }}", template)
        self.assertIn("{{ item.title }}", template)
        self.assertIn("{{ item.body_display or item.body }}", template)
        self.assertIn(".tm-notificationItem{position:relative;display:grid;gap:7px;width:100%;box-sizing:border-box;", css)
        self.assertNotRegex(css, r"\.tm-notificationItem__dismiss[^F]")
        self.assertNotIn("display: none", self._notification_css_block(css))

    def test_no_swipe_handlers_or_translated_states_remain(self):
        source = NAV_JS.read_text(encoding="utf-8")
        css = NAV_CSS.read_text(encoding="utf-8")
        template = NAV_TEMPLATE.read_text(encoding="utf-8")
        combined = "\n".join([source, self._notification_css_block(css), template])

        for obsolete in (
            "wireNotificationSwipeActions",
            "notificationSwipeReveal",
            "notificationSwipeThreshold",
            "notificationSwipeDirection",
            "openNotificationSwipe",
            "closeOpenNotificationSwipe",
            "touchstart",
            "touchmove",
            "touchend",
            "touchcancel",
            "is-dragging",
            "is-swiped",
            "translateX",
            "swipeDismiss",
        ):
            self.assertNotIn(obsolete, combined)

    def test_trash_button_is_compact_and_not_the_old_reveal_panel(self):
        css = NAV_CSS.read_text(encoding="utf-8")

        self.assertIn(".tm-notificationItem__trashAction{width:30px;height:30px;", css)
        self.assertIn(".tm-notificationItem__trashAction svg{width:16px;height:16px;", css)
        self.assertNotIn("width:76px", self._notification_css_block(css))
        self.assertNotIn("position:absolute;inset:0 0 0 auto", css)

    def _notification_css_block(self, css: str) -> str:
        match = re.search(r"\.tm-notification\{.*?\.tm-menu\{", css, re.S)
        self.assertIsNotNone(match)
        return match.group(0)


if __name__ == "__main__":
    unittest.main()
