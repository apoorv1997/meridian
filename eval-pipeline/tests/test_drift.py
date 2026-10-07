import math
import random

import pytest

from meridian_eval.drift.detector import DriftAlert, DriftDetector, _cosine_distance

EAST, NORTH = [1.0, 0.0], [0.0, 1.0]


def feed(
    det: DriftDetector, vectors: list[list[float]], tenant: str = "t", route: str = "r"
) -> list[DriftAlert]:
    return [a for v in vectors if (a := det.add(tenant, route, v)) is not None]


def test_no_alert_until_a_baseline_window_and_a_second_window_exist() -> None:
    det = DriftDetector(window_size=4)
    assert feed(det, [EAST] * 4) == []  # first full window becomes the baseline
    assert feed(det, [EAST] * 20) == []  # same direction: no drift


def test_drift_alerts_once_per_episode_and_rearms_after_recovery() -> None:
    det = DriftDetector(window_size=4, default_threshold=0.15)
    feed(det, [EAST] * 4)
    alerts = feed(det, [NORTH] * 12)
    assert len(alerts) == 1  # not one per output while it stays drifted
    assert alerts[0].distance > 0.15 and alerts[0].window_size == 4
    feed(det, [EAST] * 4)  # back to the baseline
    assert len(feed(det, [NORTH] * 4)) == 1  # a new episode alerts again


def test_per_route_threshold_overrides_the_default() -> None:
    tilted = [math.cos(0.4), math.sin(0.4)]  # cosine distance from EAST ~0.079
    loose, strict = DriftDetector(window_size=4), DriftDetector(window_size=4)
    strict.set_threshold("t", "r", 0.05)
    for det in (loose, strict):
        feed(det, [EAST] * 4)
    assert feed(loose, [tilted] * 4) == []
    assert len(feed(strict, [tilted] * 4)) == 1
    assert strict.threshold("t", "other") == 0.15  # other routes keep the default


def test_tenants_with_the_same_route_id_have_separate_baselines() -> None:
    det = DriftDetector(window_size=4)
    feed(det, [EAST] * 4, tenant="a")
    # Tenant b's first window is its own baseline, even though it points elsewhere.
    assert feed(det, [NORTH] * 4, tenant="b") == []
    assert feed(det, [NORTH] * 4, tenant="b") == []


def test_reset_baseline_takes_the_next_full_window() -> None:
    det = DriftDetector(window_size=4)
    feed(det, [EAST] * 4)
    det.reset_baseline("t", "r")
    assert feed(det, [NORTH] * 8) == []  # the NORTH window became the new baseline


def test_embedding_size_change_starts_a_new_window() -> None:
    det = DriftDetector(window_size=2)
    feed(det, [EAST] * 2)
    assert feed(det, [[0.0, 0.0, 1.0]] * 4) == []  # new model, new baseline: no bogus alert


def test_running_sum_matches_a_naive_recompute() -> None:
    rng = random.Random(3)
    window, threshold = 50, 0.02
    det = DriftDetector(window_size=window, default_threshold=threshold)
    buf: list[list[float]] = []
    baseline, drifting, expected, got = None, False, [], []
    for step in range(5_000):
        angle = step / 2_000 + rng.gauss(0, 0.3)  # slow drift plus noise
        v = [math.cos(angle) + rng.random(), math.sin(angle), rng.random()]
        if det.add("t", "r", v) is not None:
            got.append(step)
        buf = (buf + [v])[-window:]
        if len(buf) < window:
            continue
        centroid = [sum(col) / window for col in zip(*buf, strict=True)]
        if baseline is None:
            baseline = centroid
            continue
        far = _cosine_distance(centroid, baseline) > threshold
        if far and not drifting:
            expected.append(step)
        drifting = far
    assert got == expected and len(expected) > 0


def test_zero_vector_is_maximally_distant() -> None:
    assert _cosine_distance([0.0, 0.0], EAST) == 1.0


def test_window_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        DriftDetector(window_size=0)
