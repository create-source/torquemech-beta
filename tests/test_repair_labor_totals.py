import unittest

from routers.pro import repair_cost_totals, repair_labor_totals


class RepairLaborTotalsTests(unittest.TestCase):
    def test_labor_rate_takes_priority_over_legacy_labor_cost(self):
        totals = repair_cost_totals(
            {
                "labor_hours": 1.2,
                "labor_rate": 125,
                "labor_cost": 999,
                "parts_cost": 115,
            }
        )

        self.assertEqual(totals["labor_hours"], 1.2)
        self.assertEqual(totals["labor_rate"], 125)
        self.assertEqual(totals["labor_total"], 150)
        self.assertEqual(totals["parts_total"], 115)
        self.assertEqual(totals["grand_total"], 265)
        self.assertFalse(totals["labor_rate_is_legacy"])

    def test_legacy_labor_cost_is_fallback_when_labor_rate_is_missing(self):
        totals = repair_labor_totals(
            {
                "labor_hours": None,
                "labor_rate": None,
                "labor_cost": 180,
            }
        )

        self.assertIsNone(totals["labor_rate"])
        self.assertEqual(totals["labor_total"], 180)
        self.assertTrue(totals["labor_rate_is_legacy"])

    def test_tracked_parts_are_added_to_final_repair_totals(self):
        totals = repair_cost_totals(
            {
                "labor_hours": 1.0,
                "labor_rate": 120,
                "parts_cost": 25,
                "tracked_parts_total": 45,
            }
        )

        self.assertEqual(totals["parts_total"], 70)
        self.assertEqual(totals["grand_total"], 190)


if __name__ == "__main__":
    unittest.main()
