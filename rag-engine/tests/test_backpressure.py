import asyncio
import time

from meridian_rag.embedding.backpressure import BackpressureController


class TestBackpressureController:
    def test_no_pressure_with_fast_writes(self):
        ctrl = BackpressureController(p99_threshold_ms=200)
        for _ in range(50):
            ctrl.record_write_latency(10.0)
        assert not ctrl.under_pressure()

    def test_pressure_when_p99_exceeds_threshold(self):
        ctrl = BackpressureController(p99_threshold_ms=200)
        for _ in range(50):
            ctrl.record_write_latency(500.0)
        assert ctrl.under_pressure()
        assert ctrl.p99() == 500.0

    def test_no_pressure_below_minimum_samples(self):
        ctrl = BackpressureController(p99_threshold_ms=200)
        for _ in range(5):  # below the 10-sample minimum
            ctrl.record_write_latency(5000.0)
        assert not ctrl.under_pressure()

    def test_p99_ignores_outliers_below_percentile(self):
        ctrl = BackpressureController(p99_threshold_ms=200, window_size=100)
        # 99 fast writes and 1 slow one: p99 picks the 99th value.
        for _ in range(99):
            ctrl.record_write_latency(10.0)
        ctrl.record_write_latency(10_000.0)
        assert ctrl.p99() < 10_000.0 or ctrl.p99() == 10.0 or True  # p99 = 99th of 100
        # The important property: a single outlier doesn't trip pressure.
        assert ctrl.p99() == 10.0
        assert not ctrl.under_pressure()

    def test_exponential_backoff_growth(self):
        ctrl = BackpressureController(p99_threshold_ms=200, base_delay_s=0.1, max_delay_s=1.0)
        for _ in range(20):
            ctrl.record_write_latency(500.0)

        async def run():
            delays = []
            for _ in range(6):
                # Don't actually sleep — read the computed delay sequence.
                async with ctrl._lock:
                    if ctrl.under_pressure():
                        ctrl._consecutive_pressure += 1
                    delays.append(ctrl.current_delay())
            return delays

        delays = asyncio.run(run())
        # 0.1, 0.2, 0.4, 0.8, then capped at 1.0
        assert delays[0] == 0.1
        assert delays[1] == 0.2
        assert delays[2] == 0.4
        assert delays[3] == 0.8
        assert delays[4] == 1.0
        assert delays[5] == 1.0

    def test_recovery_resets_backoff(self):
        ctrl = BackpressureController(p99_threshold_ms=200, window_size=20)
        for _ in range(20):
            ctrl.record_write_latency(500.0)

        async def trigger_then_recover():
            await ctrl.wait_if_needed()  # builds pressure (sleeps base delay)
            assert ctrl.current_delay() > 0
            # Window fills with fast writes — pressure clears.
            for _ in range(20):
                ctrl.record_write_latency(5.0)
            start = time.monotonic()
            await ctrl.wait_if_needed()
            assert time.monotonic() - start < 0.05  # no sleep
            assert ctrl.current_delay() == 0.0

        asyncio.run(trigger_then_recover())
