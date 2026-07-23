import unittest
from datetime import datetime, timezone

from app.billing import resolve_subscription_access


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def subscription(status: str = "trialing", **fields):
    values = {
        "shop_id": 7,
        "plan_code": "pro_solo",
        "status": status,
        "trial_started_at": None,
        "trial_ends_at": None,
        "current_period_ends_at": None,
        "cancel_at_period_end": 0,
    }
    values.update(fields)
    return values


class SubscriptionAccessResolverTests(unittest.TestCase):
    def resolve(self, row):
        return resolve_subscription_access(row, now=NOW, shop_id=7)

    def test_active_trial_has_full_access(self):
        access = self.resolve(subscription(trial_started_at="2026-07-20T12:00:00+00:00", trial_ends_at="2026-07-24T12:00:00+00:00"))

        self.assertEqual(access.access_state, "trial_active")
        self.assertTrue(access.has_full_access)
        self.assertFalse(access.is_read_only)
        self.assertEqual(access.trial_days_remaining, 2)

    def test_final_day_of_trial_has_full_access_with_one_day_remaining(self):
        access = self.resolve(subscription(trial_started_at="2026-07-08T12:00:00+00:00", trial_ends_at="2026-07-22T12:00:01+00:00"))

        self.assertEqual(access.access_state, "trial_active")
        self.assertTrue(access.has_full_access)
        self.assertEqual(access.trial_days_remaining, 1)

    def test_expired_trial_is_read_only(self):
        access = self.resolve(subscription(trial_started_at="2026-07-01T12:00:00+00:00", trial_ends_at="2026-07-22T12:00:00+00:00"))

        self.assertEqual(access.access_state, "read_only_trial_expired")
        self.assertFalse(access.has_full_access)
        self.assertTrue(access.is_read_only)
        self.assertEqual(access.trial_days_remaining, 0)

    def test_active_subscription_has_full_access(self):
        access = self.resolve(subscription("active", current_period_ends_at="2026-08-22T12:00:00+00:00"))

        self.assertEqual(access.access_state, "subscribed_active")
        self.assertTrue(access.has_full_access)
        self.assertEqual(access.stripe_subscription_status, "active")

    def test_cancel_at_period_end_before_period_end_has_full_access(self):
        access = self.resolve(subscription("active", cancel_at_period_end=1, current_period_ends_at="2026-08-22T12:00:00+00:00"))

        self.assertEqual(access.access_state, "subscribed_canceling")
        self.assertTrue(access.has_full_access)
        self.assertTrue(access.cancel_at_period_end)

    def test_cancel_at_period_end_after_period_end_is_read_only(self):
        access = self.resolve(subscription("canceled", cancel_at_period_end=1, current_period_ends_at="2026-07-21T12:00:00+00:00"))

        self.assertEqual(access.access_state, "read_only_canceled")
        self.assertFalse(access.has_full_access)

    def test_past_due_is_read_only(self):
        access = self.resolve(subscription("past_due", current_period_ends_at="2026-08-22T12:00:00+00:00"))

        self.assertEqual(access.access_state, "read_only_past_due")
        self.assertTrue(access.is_read_only)

    def test_unpaid_is_read_only(self):
        access = self.resolve(subscription("unpaid"))

        self.assertEqual(access.access_state, "read_only_unpaid")
        self.assertTrue(access.is_read_only)

    def test_canceled_is_read_only(self):
        access = self.resolve(subscription("canceled"))

        self.assertEqual(access.access_state, "read_only_canceled")
        self.assertTrue(access.is_read_only)

    def test_no_trial_and_no_subscription_is_read_only(self):
        access = resolve_subscription_access(None, now=NOW, shop_id=7)

        self.assertEqual(access.access_state, "read_only_no_entitlement")
        self.assertFalse(access.has_full_access)
        self.assertTrue(access.is_read_only)

    def test_timezone_aware_timestamp_handling(self):
        access = self.resolve(subscription(trial_started_at="2026-07-08T08:00:00-04:00", trial_ends_at="2026-07-22T08:00:01-04:00"))

        self.assertEqual(access.access_state, "trial_active")
        self.assertEqual(access.trial_ends_at, datetime(2026, 7, 22, 12, 0, 1, tzinfo=timezone.utc))
        self.assertEqual(access.trial_days_remaining, 1)

    def test_legacy_existing_subscribed_account_remains_compatible(self):
        access = self.resolve(subscription("active"))

        self.assertEqual(access.access_state, "subscribed_active")
        self.assertTrue(access.has_full_access)

    def test_legacy_canceled_future_period_remains_compatible(self):
        row = subscription("canceled", current_period_ends_at="2026-08-22T12:00:00+00:00")
        row.pop("cancel_at_period_end")
        access = self.resolve(row)

        self.assertEqual(access.access_state, "subscribed_canceling")
        self.assertTrue(access.has_full_access)

    def test_durable_canceled_future_period_false_is_read_only(self):
        access = self.resolve(subscription("canceled", cancel_at_period_end=0, current_period_ends_at="2026-08-22T12:00:00+00:00"))

        self.assertEqual(access.access_state, "read_only_canceled")
        self.assertFalse(access.has_full_access)


if __name__ == "__main__":
    unittest.main()
