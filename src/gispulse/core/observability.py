"""
MetricsCollector — système de métriques interne pour GISPulse.

Collecte thread-safe de compteurs, gauges et histogrammes sans dépendance
externe. Exporte au format Prometheus text exposition pour compatibilité
avec tout scraper Prometheus standard.
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class _TimerContext:
    """Contexte interne retourné par MetricsCollector.timer()."""

    collector: "MetricsCollector"
    name: str
    _start: float = field(default_factory=time.monotonic, init=False)

    def __enter__(self) -> "_TimerContext":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_exc) -> None:
        elapsed = time.monotonic() - self._start
        self.collector.observe(self.name, elapsed)


class MetricsCollector:
    """Collecteur de métriques thread-safe pour GISPulse.

    Fournit trois types de métriques :
    - **counter** : valeur entière monotone croissante (``inc``).
    - **gauge**   : valeur numérique instantanée, libre (``gauge``).
    - **histogram**: série d'observations (``observe``), résumée via
      ``count``, ``sum``, ``min``, ``max`` et ``avg``.

    Usage::

        m = MetricsCollector.get()
        m.inc("jobs_total")
        m.gauge("active_workers", 3.0)
        m.observe("job_duration_seconds", 1.25)

        with m.timer("job_duration_seconds"):
            run_job()

        print(m.to_prometheus_text())
    """

    _instance: "MetricsCollector | None" = None
    _instance_lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> "MetricsCollector":
        """Retourne l'instance singleton (thread-safe)."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # Max observations per histogram to prevent unbounded memory growth
    _HISTOGRAM_MAX_LEN = 10_000

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=MetricsCollector._HISTOGRAM_MAX_LEN)
        )
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def inc(self, name: str, value: int = 1) -> None:
        """Incrémente un compteur de ``value`` (défaut 1)."""
        with self._lock:
            self._counters[name] += value

    def gauge(self, name: str, value: float) -> None:
        """Fixe la valeur d'une gauge."""
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        """Ajoute une observation à un histogramme."""
        with self._lock:
            self._histograms[name].append(value)

    # ------------------------------------------------------------------
    # Timer context manager
    # ------------------------------------------------------------------

    def timer(self, name: str) -> _TimerContext:
        """Context manager mesurant la durée d'un bloc et l'observant."""
        return _TimerContext(collector=self, name=name)

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Retourne un snapshot instantané de toutes les métriques."""
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {
                name: self._summarise(list(values))
                for name, values in self._histograms.items()
            }
        return {
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Réinitialise toutes les métriques (utile pour les tests)."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    # ------------------------------------------------------------------
    # Export Prometheus
    # ------------------------------------------------------------------

    def to_prometheus_text(self) -> str:
        """Exporte les métriques au format Prometheus text exposition."""
        lines: list[str] = []

        with self._lock:
            for name, value in sorted(self._counters.items()):
                lines.append(f"# HELP {name} GISPulse counter metric")
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")

            for name, value in sorted(self._gauges.items()):
                lines.append(f"# HELP {name} GISPulse gauge metric")
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {value}")

            for name, values in sorted(self._histograms.items()):
                summary = self._summarise(list(values))
                lines.append(f"# HELP {name} GISPulse histogram metric")
                lines.append(f"# TYPE {name} summary")
                lines.append(f"{name}_count {summary['count']}")
                lines.append(f"{name}_sum {summary['sum']}")
                if summary["count"] > 0:
                    lines.append(f"{name}_min {summary['min']}")
                    lines.append(f"{name}_max {summary['max']}")

        return "\n".join(lines) + ("\n" if lines else "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _summarise(values: list[float]) -> dict:
        """Calcule les statistiques d'une liste d'observations."""
        if not values:
            return {"count": 0, "sum": 0.0, "min": None, "max": None, "avg": None, "values": []}
        total = sum(values)
        return {
            "count": len(values),
            "sum": total,
            "min": min(values),
            "max": max(values),
            "avg": total / len(values),
            "values": values,
        }
