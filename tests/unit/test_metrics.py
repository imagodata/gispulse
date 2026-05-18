"""
Tests unitaires pour adapters/metrics.py — MetricsCollector.
"""

from __future__ import annotations

import threading
import time

import pytest

from gispulse.core.observability import MetricsCollector


@pytest.fixture(autouse=True)
def fresh_metrics():
    """Reset le singleton avant chaque test pour isoler les cas."""
    MetricsCollector.get().reset()
    yield
    MetricsCollector.get().reset()


# ---------------------------------------------------------------------------
# inc / gauge / observe
# ---------------------------------------------------------------------------

class TestInc:
    def test_inc_default_value(self):
        m = MetricsCollector.get()
        m.inc("jobs_total")
        snap = m.snapshot()
        assert snap["counters"]["jobs_total"] == 1

    def test_inc_custom_value(self):
        m = MetricsCollector.get()
        m.inc("jobs_total", 5)
        assert m.snapshot()["counters"]["jobs_total"] == 5

    def test_inc_accumulates(self):
        m = MetricsCollector.get()
        m.inc("jobs_total")
        m.inc("jobs_total")
        m.inc("jobs_total", 3)
        assert m.snapshot()["counters"]["jobs_total"] == 5

    def test_inc_multiple_counters_independent(self):
        m = MetricsCollector.get()
        m.inc("alpha")
        m.inc("beta", 10)
        snap = m.snapshot()
        assert snap["counters"]["alpha"] == 1
        assert snap["counters"]["beta"] == 10


class TestGauge:
    def test_gauge_set(self):
        m = MetricsCollector.get()
        m.gauge("active_workers", 4.0)
        assert m.snapshot()["gauges"]["active_workers"] == 4.0

    def test_gauge_overwrite(self):
        m = MetricsCollector.get()
        m.gauge("active_workers", 4.0)
        m.gauge("active_workers", 2.0)
        assert m.snapshot()["gauges"]["active_workers"] == 2.0

    def test_gauge_zero(self):
        m = MetricsCollector.get()
        m.gauge("queue_depth", 0.0)
        assert m.snapshot()["gauges"]["queue_depth"] == 0.0


class TestObserve:
    def test_observe_adds_value(self):
        m = MetricsCollector.get()
        m.observe("job_duration_seconds", 1.5)
        snap = m.snapshot()
        hist = snap["histograms"]["job_duration_seconds"]
        assert hist["count"] == 1
        assert hist["sum"] == pytest.approx(1.5)
        assert hist["min"] == pytest.approx(1.5)
        assert hist["max"] == pytest.approx(1.5)

    def test_observe_multiple(self):
        m = MetricsCollector.get()
        for v in [1.0, 2.0, 3.0]:
            m.observe("job_duration_seconds", v)
        snap = m.snapshot()["histograms"]["job_duration_seconds"]
        assert snap["count"] == 3
        assert snap["sum"] == pytest.approx(6.0)
        assert snap["min"] == pytest.approx(1.0)
        assert snap["max"] == pytest.approx(3.0)
        assert snap["avg"] == pytest.approx(2.0)

    def test_observe_empty_histogram_absent(self):
        m = MetricsCollector.get()
        snap = m.snapshot()
        assert "job_duration_seconds" not in snap["histograms"]


# ---------------------------------------------------------------------------
# Timer context manager
# ---------------------------------------------------------------------------

class TestTimer:
    def test_timer_records_duration(self):
        m = MetricsCollector.get()
        with m.timer("test_timer_duration"):
            time.sleep(0.05)
        snap = m.snapshot()["histograms"]["test_timer_duration"]
        assert snap["count"] == 1
        # Tolérance large pour les environnements lents
        assert snap["min"] >= 0.04
        assert snap["max"] < 1.0

    def test_timer_accumulates(self):
        m = MetricsCollector.get()
        with m.timer("op_duration"):
            pass
        with m.timer("op_duration"):
            pass
        snap = m.snapshot()["histograms"]["op_duration"]
        assert snap["count"] == 2

    def test_timer_records_even_on_exception(self):
        m = MetricsCollector.get()
        with pytest.raises(ValueError):
            with m.timer("errored_op"):
                raise ValueError("boom")
        snap = m.snapshot()["histograms"]["errored_op"]
        assert snap["count"] == 1


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    def test_snapshot_contains_all_types(self):
        m = MetricsCollector.get()
        m.inc("c1")
        m.gauge("g1", 7.0)
        m.observe("h1", 0.5)
        snap = m.snapshot()
        assert "c1" in snap["counters"]
        assert "g1" in snap["gauges"]
        assert "h1" in snap["histograms"]

    def test_snapshot_is_copy_not_reference(self):
        """Modifier le snapshot ne doit pas modifier l'état interne."""
        m = MetricsCollector.get()
        m.inc("jobs_total", 3)
        snap = m.snapshot()
        snap["counters"]["jobs_total"] = 9999
        assert m.snapshot()["counters"]["jobs_total"] == 3

    def test_snapshot_empty_on_fresh_collector(self):
        m = MetricsCollector.get()
        snap = m.snapshot()
        assert snap["counters"] == {}
        assert snap["gauges"] == {}
        assert snap["histograms"] == {}


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

class TestReset:
    def test_reset_clears_all(self):
        m = MetricsCollector.get()
        m.inc("jobs_total", 10)
        m.gauge("workers", 3.0)
        m.observe("duration", 1.0)
        m.reset()
        snap = m.snapshot()
        assert snap["counters"] == {}
        assert snap["gauges"] == {}
        assert snap["histograms"] == {}

    def test_reset_allows_reuse(self):
        m = MetricsCollector.get()
        m.inc("jobs_total", 5)
        m.reset()
        m.inc("jobs_total", 2)
        assert m.snapshot()["counters"]["jobs_total"] == 2


# ---------------------------------------------------------------------------
# to_prometheus_text
# ---------------------------------------------------------------------------

class TestPrometheusText:
    def test_counter_format(self):
        m = MetricsCollector.get()
        m.inc("jobs_total", 7)
        text = m.to_prometheus_text()
        assert "# TYPE jobs_total counter" in text
        assert "jobs_total 7" in text

    def test_gauge_format(self):
        m = MetricsCollector.get()
        m.gauge("active_workers", 3.0)
        text = m.to_prometheus_text()
        assert "# TYPE active_workers gauge" in text
        assert "active_workers 3.0" in text

    def test_histogram_summary_format(self):
        m = MetricsCollector.get()
        m.observe("job_duration_seconds", 1.0)
        m.observe("job_duration_seconds", 3.0)
        text = m.to_prometheus_text()
        assert "# TYPE job_duration_seconds summary" in text
        assert "job_duration_seconds_count 2" in text
        assert "job_duration_seconds_sum 4.0" in text
        assert "job_duration_seconds_min 1.0" in text
        assert "job_duration_seconds_max 3.0" in text

    def test_empty_returns_empty_string_or_newline(self):
        m = MetricsCollector.get()
        text = m.to_prometheus_text()
        assert text == "" or text == "\n"

    def test_ends_with_newline_when_non_empty(self):
        m = MetricsCollector.get()
        m.inc("x", 1)
        assert m.to_prometheus_text().endswith("\n")

    def test_help_line_present(self):
        m = MetricsCollector.get()
        m.inc("jobs_total")
        text = m.to_prometheus_text()
        assert "# HELP jobs_total" in text


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_inc(self):
        """Deux threads incrémentent le même compteur en parallèle."""
        m = MetricsCollector.get()
        iterations = 1000

        def worker():
            for _ in range(iterations):
                m.inc("concurrent_counter")

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert m.snapshot()["counters"]["concurrent_counter"] == iterations * 2

    def test_concurrent_observe(self):
        """Deux threads ajoutent des observations à l'histogramme."""
        m = MetricsCollector.get()
        iterations = 500

        def worker():
            for _ in range(iterations):
                m.observe("concurrent_hist", 1.0)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        snap = m.snapshot()["histograms"]["concurrent_hist"]
        assert snap["count"] == iterations * 2
