"""Pruebas del diagnostico de latencia y de tiempos de procesado.

El contrato fija tres piezas: `RollingStats` (percentiles con ventana acotada),
`LatencyBreakdown` (desglose en ms del camino de audio) y los formateadores
`format_stats` / `LatencyBreakdown.format` (lineas de una fila tolerantes a
diccionarios incompletos).
"""

import math

import pytest

from audio_enhancer.diagnostics import (
    LatencyBreakdown,
    RollingStats,
    format_stats,
    latency_breakdown,
)

CLAVES_SNAPSHOT = ("p50_ms", "p95_ms", "p99_ms", "max_ms", "mean_ms")
CLAVES_BREAKDOWN = (
    "capture_block_ms",
    "ring_ms",
    "child_backlog_ms",
    "output_block_ms",
    "total_ms",
)
ETAPAS = ("capture_block_ms", "ring_ms", "child_backlog_ms", "output_block_ms")


def test_rolling_stats_sin_muestras_devuelve_ceros():
    stats = RollingStats()

    assert stats.count == 0
    snapshot = stats.snapshot()
    assert tuple(snapshot) == CLAVES_SNAPSHOT
    assert all(snapshot[clave] == 0.0 for clave in CLAVES_SNAPSHOT)


def test_rolling_stats_percentiles_con_datos_conocidos():
    # Interpolacion lineal (metodo R-7) sobre [1, 2, 3, 4] ms:
    # p50 -> rango 1.5, p95 -> rango 2.85, p99 -> rango 2.97.
    stats = RollingStats()
    for milisegundos in (1.0, 2.0, 3.0, 4.0):
        stats.add_ms(milisegundos)

    snapshot = stats.snapshot()

    assert snapshot["p50_ms"] == pytest.approx(2.5)
    assert snapshot["p95_ms"] == pytest.approx(3.85)
    assert snapshot["p99_ms"] == pytest.approx(3.97)
    assert snapshot["max_ms"] == pytest.approx(4.0)
    assert snapshot["mean_ms"] == pytest.approx(2.5)


def test_rolling_stats_percentiles_son_deterministas_e_independientes_del_orden():
    desordenado = RollingStats()
    for milisegundos in (4.0, 1.0, 3.0, 2.0):
        desordenado.add_ms(milisegundos)
    ordenado = RollingStats()
    for milisegundos in (1.0, 2.0, 3.0, 4.0):
        ordenado.add_ms(milisegundos)

    primero = desordenado.snapshot()
    segundo = ordenado.snapshot()

    for clave in CLAVES_SNAPSHOT:
        assert primero[clave] == pytest.approx(segundo[clave])


def test_rolling_stats_ventana_acotada_descarta_las_muestras_viejas():
    stats = RollingStats(window=4)
    for milisegundos in (10.0, 20.0, 30.0, 40.0, 50.0):
        stats.add_ms(milisegundos)

    snapshot = stats.snapshot()

    # count es historico; la ventana solo conserva [20, 30, 40, 50].
    assert stats.count == 5
    assert snapshot["max_ms"] == pytest.approx(50.0)
    assert snapshot["mean_ms"] == pytest.approx(35.0)
    assert snapshot["p50_ms"] == pytest.approx(35.0)


def test_rolling_stats_ventana_invalida_cae_a_una_muestra():
    stats = RollingStats(window=0)
    stats.add_ms(1.0)
    stats.add_ms(2.0)

    snapshot = stats.snapshot()

    assert stats.count == 2
    assert snapshot["p50_ms"] == pytest.approx(2.0)
    assert snapshot["max_ms"] == pytest.approx(2.0)


def test_rolling_stats_ignora_valores_no_finitos_y_sanea_negativos():
    stats = RollingStats()
    stats.add(float("nan"))
    stats.add(math.inf)
    stats.add_ms(-5.0)

    snapshot = stats.snapshot()

    # nan/inf se descartan (no envenenan la estadistica) y -5 ms se acota a 0.
    assert stats.count == 1
    assert snapshot["p50_ms"] == pytest.approx(0.0)
    assert snapshot["max_ms"] == pytest.approx(0.0)


def test_rolling_stats_clear_reinicia_la_serie():
    stats = RollingStats()
    stats.add_ms(5.0)
    stats.clear()

    assert stats.count == 0
    assert stats.snapshot()["max_ms"] == 0.0


def test_rolling_stats_add_acepta_segundos():
    stats = RollingStats()
    stats.add(0.002)

    assert stats.snapshot()["max_ms"] == pytest.approx(2.0)


def test_latency_breakdown_desglose_por_etapas_y_total_es_la_suma():
    breakdown = LatencyBreakdown(sample_rate=48000, block_frames=1024)

    snapshot = breakdown.compute(ring_fill_frames=2880, backlog_blocks=2.0)

    bloque_ms = 1024 / 48000 * 1000.0
    assert snapshot["capture_block_ms"] == pytest.approx(bloque_ms)
    assert snapshot["output_block_ms"] == pytest.approx(bloque_ms)
    assert snapshot["ring_ms"] == pytest.approx(60.0)
    assert snapshot["child_backlog_ms"] == pytest.approx(2.0 * bloque_ms)
    assert snapshot["total_ms"] == pytest.approx(sum(snapshot[etapa] for etapa in ETAPAS))


def test_latency_breakdown_clampa_relleno_y_cola_negativos():
    breakdown = LatencyBreakdown(sample_rate=48000, block_frames=1024)

    snapshot = breakdown.compute(ring_fill_frames=-9999, backlog_blocks=-3.5)

    assert snapshot["ring_ms"] == 0.0
    assert snapshot["child_backlog_ms"] == 0.0
    assert snapshot["total_ms"] == pytest.approx(snapshot["capture_block_ms"] + snapshot["output_block_ms"])


def test_latency_breakdown_sin_datos_solo_cuenta_los_bloques():
    snapshot = LatencyBreakdown().compute()

    assert snapshot["ring_ms"] == 0.0
    assert snapshot["child_backlog_ms"] == 0.0
    assert snapshot["total_ms"] == pytest.approx(2.0 * 1024 / 48000 * 1000.0)


@pytest.mark.parametrize(
    ("sample_rate", "block_frames", "ring_fill_frames", "backlog_blocks"),
    [
        (0, -1024, -1, float("nan")),
        (-48000, 0, -1.0, -5.0),
        (48000, float("inf"), 0, float("inf")),
    ],
)
def test_latency_breakdown_nunca_lanza_ni_devuelve_valores_no_finitos(
    sample_rate, block_frames, ring_fill_frames, backlog_blocks
):
    breakdown = LatencyBreakdown(sample_rate=sample_rate, block_frames=block_frames)
    snapshot = breakdown.compute(ring_fill_frames=ring_fill_frames, backlog_blocks=backlog_blocks)

    assert tuple(snapshot) == CLAVES_BREAKDOWN
    assert all(math.isfinite(valor) and valor >= 0.0 for valor in snapshot.values())


def test_latency_breakdown_funcion_suelta_equivale_a_la_clase():
    suelta = latency_breakdown(48000, 1024, 2880, 2.0)
    clase = LatencyBreakdown(48000, 1024).compute(ring_fill_frames=2880, backlog_blocks=2.0)

    assert suelta == clase


def test_format_breakdown_produce_una_sola_fila():
    linea = LatencyBreakdown.format(
        {
            "capture_block_ms": 21.3,
            "ring_ms": 60.0,
            "child_backlog_ms": 0.0,
            "output_block_ms": 21.3,
            "total_ms": 102.6,
        }
    )

    assert linea == "bloque 21.3 | ring 60.0 | cola hijo 0.0 | salida 21.3 | total 102.6 ms"


def test_format_breakdown_tolera_diccionario_vacio_o_incompleto():
    vacio = LatencyBreakdown.format({})
    incompleto = LatencyBreakdown.format({"ring_ms": 60.0})

    assert vacio == "bloque 0.0 | ring 0.0 | cola hijo 0.0 | salida 0.0 | total 0.0 ms"
    assert incompleto == "bloque 0.0 | ring 60.0 | cola hijo 0.0 | salida 0.0 | total 0.0 ms"


def test_format_stats_tolera_claves_ausentes():
    assert format_stats({}) == "p50 0.0 ms | p95 0.0 | p99 0.0 | max 0.0"
    assert format_stats({"p50_ms": 1.2, "max_ms": 3.1}) == "p50 1.2 ms | p95 0.0 | p99 0.0 | max 3.1"


def test_format_stats_acepta_el_snapshot_de_rolling_stats():
    stats = RollingStats()
    for milisegundos in (10.0, 20.0):
        stats.add_ms(milisegundos)

    assert format_stats(stats.snapshot()) == "p50 15.0 ms | p95 19.5 | p99 19.9 | max 20.0"
