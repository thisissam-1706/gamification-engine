"""The benchmark runner reproduces the experiment engine from a persisted log it builds itself."""


def test_small_benchmark_reports_a_bit_identical_engine_reproduction():
    from scripts.run_scale_benchmark import run_benchmark

    report = run_benchmark("small")

    assert report["engine_repro"]["bit_identical"] is True
    assert report["engine_repro"]["population_size"] == report["profile"]["population_size"]
