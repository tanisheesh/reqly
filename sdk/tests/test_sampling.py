from reqly.core.sampling import Sampler


def test_full_sample_rate_always_keeps():
    sampler = Sampler(sample_rate=1.0)
    for _ in range(20):
        assert sampler.should_sample() is True
    stats = sampler.stats()
    assert stats["observed_count"] == 20
    assert stats["sampled_count"] == 20


def test_zero_sample_rate_never_keeps_but_still_counts_observed():
    sampler = Sampler(sample_rate=0.0)
    for _ in range(10):
        assert sampler.should_sample() is False
    stats = sampler.stats()
    assert stats["observed_count"] == 10
    assert stats["sampled_count"] == 0


def test_sample_rate_clamped_to_valid_range():
    assert Sampler(sample_rate=5.0).sample_rate == 1.0
    assert Sampler(sample_rate=-1.0).sample_rate == 0.0
