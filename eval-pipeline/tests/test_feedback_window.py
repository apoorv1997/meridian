import pytest

from meridian_eval.feedback.window import FeedbackWindow


class FakeUpdater:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, dict[str, float]]] = []
        self.fail = fail

    async def apply(
        self, tenant_id: str, route_id: str, deltas: dict[str, float]
    ) -> dict[str, float]:
        self.calls.append((tenant_id, route_id, deltas))
        if self.fail:
            raise ConnectionError("redis down")
        return {}


async def record_many(
    window: FeedbackWindow, route: str, provider: str, scores: list[float]
) -> None:
    for s in scores:
        await window.record("t", route, provider, s)


async def test_mean_score_maps_linearly_to_a_delta() -> None:
    updater = FakeUpdater()
    window = FeedbackWindow(updater, min_samples=3, max_shift=0.10)
    await record_many(window, "r1", "good", [0.7, 0.7, 0.7])
    await record_many(window, "r1", "bad", [0.3, 0.3, 0.3])
    await record_many(window, "r1", "even", [0.5, 0.5, 0.5])
    await window._flush()
    [(tenant, route, deltas)] = updater.calls
    assert (tenant, route) == ("t", "r1")
    assert deltas == pytest.approx({"good": 0.04, "bad": -0.04, "even": 0.0})


async def test_extreme_scores_give_the_full_shift() -> None:
    updater = FakeUpdater()
    window = FeedbackWindow(updater, min_samples=1, max_shift=0.10)
    await window.record("t", "r1", "perfect", 1.0)
    await window.record("t", "r1", "useless", 0.0)
    await window._flush()
    assert updater.calls[0][2] == pytest.approx({"perfect": 0.10, "useless": -0.10})


async def test_providers_under_the_sample_minimum_get_no_delta() -> None:
    updater = FakeUpdater()
    window = FeedbackWindow(updater, min_samples=3)
    await record_many(window, "r1", "enough", [0.9, 0.9, 0.9])
    await record_many(window, "r1", "few", [0.9, 0.9])
    await record_many(window, "r2", "few", [0.1])
    await window._flush()
    assert updater.calls == [("t", "r1", pytest.approx({"enough": 0.08}))]  # r2: nothing to send


async def test_failed_judge_scores_are_ignored() -> None:
    updater = FakeUpdater()
    window = FeedbackWindow(updater, min_samples=2)
    await record_many(window, "r1", "p", [-1.0, -1.0, 0.8])
    await window._flush()
    assert updater.calls == []  # only one valid score: under the minimum


async def test_each_window_starts_empty() -> None:
    updater = FakeUpdater()
    window = FeedbackWindow(updater, min_samples=2)
    await record_many(window, "r1", "p", [0.9, 0.9])
    await window._flush()
    await window._flush()  # nothing recorded since: no second update
    assert len(updater.calls) == 1


async def test_updater_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    window = FeedbackWindow(FakeUpdater(fail=True), min_samples=1)
    await window.record("t", "r1", "p", 0.9)
    await window._flush()
    assert "weight update failed" in caplog.text


async def test_same_route_id_in_two_tenants_is_scored_separately() -> None:
    updater = FakeUpdater()
    window = FeedbackWindow(updater, min_samples=2)
    for _ in range(2):
        await window.record("tenant-a", "default", "p", 0.9)
        await window.record("tenant-b", "default", "p", 0.1)
    await window._flush()
    by_tenant = {tenant: deltas for tenant, _, deltas in updater.calls}
    assert by_tenant == {
        "tenant-a": pytest.approx({"p": 0.08}),
        "tenant-b": pytest.approx({"p": -0.08}),
    }
