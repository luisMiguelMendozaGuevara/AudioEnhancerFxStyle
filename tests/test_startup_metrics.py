from audio_enhancer.startup_metrics import StartupMetrics


def test_startup_metrics_reports_elapsed_ms_in_insertion_order():
    ticks = iter((10.0, 10.125, 10.500))
    metrics = StartupMetrics(clock=lambda: next(ticks))

    metrics.mark("root_created")
    metrics.mark("first_paint")
    metrics.mark("ui_ready")

    assert metrics.elapsed_ms("root_created", "first_paint") == 125.0
    assert metrics.elapsed_ms("root_created", "ui_ready") == 500.0


def test_startup_metrics_summary_preserves_marked_values():
    metrics = StartupMetrics(clock=lambda: 42.0)
    metrics.mark("root_created")
    metrics.mark("ui_ready")

    assert metrics.summary() == {"root_created": 42.0, "ui_ready": 42.0}
