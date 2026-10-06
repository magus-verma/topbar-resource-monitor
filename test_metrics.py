"""Verify the resource math and warning timing without stressing the laptop."""

import unittest

from metrics import AlertState, cpu_counters, cpu_percent, level_for, memory_usage


class MetricsTests(unittest.TestCase):
    def test_cpu_does_not_double_count_guest_time(self):
        self.assertEqual(cpu_counters("cpu 100 0 50 800 20 10 10 10 40 0\n"), (1000, 820))
        self.assertAlmostEqual(cpu_percent((1000, 820), (1200, 920)), 50)

    def test_reset_counters_are_unavailable(self):
        self.assertIsNone(cpu_percent((1000, 820), (900, 700)))
        self.assertIsNone(cpu_percent((1000, 820), (1200, 700)))

    def test_reclaimable_cache_is_available_ram(self):
        sample = memory_usage(
            "MemTotal: 10000 kB\nMemAvailable: 3000 kB\nMemFree: 1000 kB\nSwapTotal: 2000 kB\nSwapFree: 1500 kB\n"
        )
        self.assertEqual(sample, (70, 7000, 10000, 3000, 500, 2000, 25))

    def test_full_swap_and_no_swap(self):
        base = "MemTotal: 10000 kB\nMemAvailable: 3000 kB\n"
        full = memory_usage(base + "SwapTotal: 2000 kB\nSwapFree: 0 kB\n")
        self.assertEqual(full[4:], (2000, 2000, 100))
        disabled = memory_usage(base + "SwapTotal: 0 kB\nSwapFree: 0 kB\n")
        self.assertEqual(disabled[4:], (0, 0, None))
        self.assertEqual(level_for(disabled[-1], 80, 95), 0)
        self.assertEqual(AlertState().update(disabled[-1], 100), 0)

    def test_swap_counters_are_clamped(self):
        base = "MemTotal: 10000 kB\nMemAvailable: 3000 kB\nSwapTotal: 2000 kB\n"
        self.assertEqual(memory_usage(base + "SwapFree: 3000 kB\n")[-1], 0)
        self.assertEqual(memory_usage(base + "SwapFree: -500 kB\n")[-1], 100)

    def test_colour_boundaries(self):
        for value, expected in ((79.99, 0), (80, 1), (94.99, 1), (95, 2), (100, 2)):
            self.assertEqual(level_for(value, 80, 95), expected)

    def test_spike_does_not_notify(self):
        alert = AlertState()
        self.assertEqual(alert.update(90, 0), 0)
        self.assertEqual(alert.update(20, 2), 0)
        self.assertEqual(alert.update(90, 4), 0)
        self.assertEqual(alert.update(90, 12), 0)
        self.assertEqual(alert.update(90, 14), 1)

    def test_critical_escalates_and_repeats_are_limited(self):
        alert = AlertState()
        self.assertEqual(alert.update(85, 0), 0)
        self.assertEqual(alert.update(85, 10), 1)
        self.assertEqual(alert.update(96, 12), 0)
        self.assertEqual(alert.update(96, 20), 0)
        self.assertEqual(alert.update(96, 22), 2)
        self.assertEqual(alert.update(96, 300), 0)
        self.assertEqual(alert.update(96, 322), 2)

    def test_recovery_rearms_with_hysteresis(self):
        alert = AlertState()
        alert.update(90, 0)
        self.assertEqual(alert.update(90, 10), 1)
        alert.update(79, 12)
        alert.update(90, 14)
        self.assertEqual(alert.update(90, 24), 0)
        alert.update(70, 26)
        alert.update(90, 28)
        self.assertEqual(alert.update(90, 38), 1)

    def test_unavailable_gap_does_not_count_as_sustained(self):
        alert = AlertState()
        alert.update(96, 0)
        alert.update(None, 8)
        self.assertEqual(alert.update(96, 30), 0)
        self.assertEqual(alert.update(96, 40), 2)


if __name__ == "__main__":
    unittest.main()
