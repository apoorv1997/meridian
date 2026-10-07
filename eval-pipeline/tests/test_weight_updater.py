"""WeightUpdater against an in-memory Redis that runs the real Lua script (fakeredis + lupa)."""

import random

import fakeredis
import pytest

from meridian_eval.feedback.weight_updater import WeightUpdater, route_weights_key

FLOOR = 0.05


@pytest.fixture
async def redis() -> fakeredis.FakeAsyncRedis:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


async def stored(redis: fakeredis.FakeAsyncRedis, route: str = "r1") -> dict[str, float]:
    return {p: float(w) for p, w in (await redis.hgetall(route_weights_key(route))).items()}


def test_key_matches_the_gateway() -> None:
    # The gateway reads this exact key: weightsKey() in gateway/internal/router/weighted.go.
    assert route_weights_key("r1") == "route:r1:weights"


async def test_opposite_deltas_shift_weight(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.hset(route_weights_key("r1"), mapping={"a": 0.5, "b": 0.5})
    returned = await WeightUpdater(redis).apply("r1", {"a": 0.10, "b": -0.10})
    assert await stored(redis) == pytest.approx({"a": 0.6, "b": 0.4})
    assert returned == pytest.approx({"a": 0.6, "b": 0.4})


async def test_providers_without_a_delta_are_renormalised(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.hset(route_weights_key("r1"), mapping={"a": 0.5, "b": 0.3, "c": 0.2})
    await WeightUpdater(redis).apply("r1", {"a": 0.1, "b": 0.1})
    w = await stored(redis)
    assert w == pytest.approx({"a": 0.6 / 1.2, "b": 0.4 / 1.2, "c": 0.2 / 1.2})


async def test_delta_is_clamped_to_max_shift(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.hset(route_weights_key("r1"), mapping={"a": 0.5, "b": 0.5})
    await WeightUpdater(redis).apply("r1", {"a": 0.9})
    assert await stored(redis) == pytest.approx({"a": 0.6 / 1.1, "b": 0.5 / 1.1})


async def test_new_route_starts_with_equal_shares(redis: fakeredis.FakeAsyncRedis) -> None:
    await WeightUpdater(redis).apply("r1", {"a": 0.0, "b": 0.0, "c": 0.0})
    assert await stored(redis) == pytest.approx({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})


async def test_non_numeric_weight_is_treated_as_missing(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.hset(route_weights_key("r1"), mapping={"a": "junk", "b": 0.5})
    await WeightUpdater(redis).apply("r1", {"b": 0.0})
    assert await stored(redis) == pytest.approx({"a": 0.5, "b": 0.5})


async def test_floor_holds_after_renormalising(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.hset(route_weights_key("r1"), mapping={"a": 0.95, "b": 0.05})
    await WeightUpdater(redis).apply("r1", {"a": 0.10, "b": -0.10})
    assert await stored(redis) == pytest.approx({"a": 0.95, "b": FLOOR})


async def test_floor_cascades_to_every_provider(redis: fakeredis.FakeAsyncRedis) -> None:
    # After renormalising, d is 0.0517: above the floor. Raising b and c to the floor
    # scales d down to 0.0466, under it, so a second round has to floor d as well.
    await redis.hset(route_weights_key("r1"), mapping={"a": 0.88, "b": 0.0, "c": 0.0, "d": 0.048})
    await WeightUpdater(redis).apply("r1", {"a": 0.0})
    assert await stored(redis) == pytest.approx({"a": 0.85, "b": FLOOR, "c": FLOOR, "d": FLOOR})


async def test_impossible_floor_falls_back_to_equal_shares(redis: fakeredis.FakeAsyncRedis) -> None:
    providers = [f"p{i}" for i in range(25)]  # 25 * 5% > 100%
    await WeightUpdater(redis).apply("r1", {p: 0.0 for p in providers})
    assert await stored(redis) == pytest.approx({p: 1 / 25 for p in providers})


async def test_result_does_not_depend_on_delta_order(redis: fakeredis.FakeAsyncRedis) -> None:
    deltas = {"a": 0.07, "b": -0.03, "c": 0.02}
    await WeightUpdater(redis).apply("r1", deltas)
    await WeightUpdater(redis).apply("r2", dict(reversed(deltas.items())))
    assert await stored(redis, "r1") == pytest.approx(await stored(redis, "r2"))


async def test_invariants_hold_for_random_updates(redis: fakeredis.FakeAsyncRedis) -> None:
    rng = random.Random(7)
    updater = WeightUpdater(redis)
    for case in range(300):
        route = f"route-{case}"
        n = rng.randint(2, 8)
        start = [rng.random() for _ in range(n)]
        await redis.hset(
            route_weights_key(route),
            mapping={f"p{i}": w / sum(start) for i, w in enumerate(start)},
        )
        before = await stored(redis, route)
        deltas = {f"p{i}": rng.uniform(-0.3, 0.3) for i in rng.sample(range(n), rng.randint(1, n))}
        await updater.apply(route, deltas)
        after = await stored(redis, route)
        assert set(after) == set(before), case
        assert sum(after.values()) == pytest.approx(1.0), case
        assert min(after.values()) >= FLOOR - 1e-12, case


async def test_reloads_the_script_after_a_redis_restart(redis: fakeredis.FakeAsyncRedis) -> None:
    updater = WeightUpdater(redis)
    await updater.apply("r1", {"a": 0.0, "b": 0.0})
    await redis.script_flush()  # what a restart does to cached scripts
    await updater.apply("r1", {"a": 0.1, "b": -0.1})
    assert await stored(redis) == pytest.approx({"a": 0.6, "b": 0.4})


async def test_no_deltas_writes_nothing(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await WeightUpdater(redis).apply("r1", {}) == {}
    assert await redis.exists(route_weights_key("r1")) == 0
