"""Tests for Phase 6: Analysis consistency across violations, hotspots, and CSV exports."""

import numpy as np
import pytest

from irdrop import GDSLayout, PDNBuilder, IRDropSolver, IRDropAnalyzer, run_analysis


def test_bottleneck_sample_consistency():
    """Verify violation count, hotspot area, and CSV row count agree with each other."""
    layout, result, analysis, _ = run_analysis(
        "samples/bottleneck_pdn.gds",
        v_nom=1.0,
        delta_v_limit_mv=45.0,
        total_current=0.4,
        distribution="uniform",
        grid_resolution=(100, 100),
        tech="default",
        max_violation_rows=5000,
    )

    assert analysis.status == "VIOLATION"
    assert analysis.violating_node_count > 0

    # 1. Active cell area consistency
    dx = float(result.x_coords_um[1] - result.x_coords_um[0])
    dy = float(result.y_coords_um[1] - result.y_coords_um[0])
    cell_area = dx * dy
    expected_area = analysis.violating_node_count * cell_area

    assert pytest.approx(analysis.violating_area_um2, rel=1e-3) == expected_area

    # 2. Hotspot area consistency: sum of all hotspot areas must equal total violating area
    sum_hotspot_area = sum(h.area_um2 for h in analysis.hotspots)
    assert pytest.approx(analysis.violating_area_um2, rel=1e-2) == sum_hotspot_area

    # 3. CSV row count consistency: when cap >= violating_node_count, count must match
    assert len(analysis.violating_nodes) == analysis.violating_node_count

    # 4. CSV ordering: must be sorted worst-first
    drops = [node["drop_mv"] for node in analysis.violating_nodes]
    assert drops[0] == pytest.approx(analysis.delta_v_max_mv, abs=0.01)
    for i in range(len(drops) - 1):
        assert drops[i] >= drops[i + 1], f"Violations not sorted worst-first: {drops[i]} < {drops[i+1]}"


def test_explicit_csv_row_cap():
    """Verify max_violation_rows explicitly caps CSV export while preserving worst-first order."""
    layout, result, analysis, _ = run_analysis(
        "samples/bottleneck_pdn.gds",
        v_nom=1.0,
        delta_v_limit_mv=45.0,
        total_current=0.4,
        distribution="uniform",
        grid_resolution=(100, 100),
        tech="default",
        max_violation_rows=10,
    )

    assert analysis.violating_node_count > 10
    assert len(analysis.violating_nodes) == 10
    assert analysis.violating_nodes[0]["drop_mv"] == pytest.approx(analysis.delta_v_max_mv, abs=0.01)


def test_single_cell_hotspot_retained():
    """Verify isolated single-cell violations are retained as hotspots."""
    # Create synthetic layout where only one cell exceeds limit
    layout = GDSLayout("samples/mesh_pdn.gds")
    builder = PDNBuilder(layout, grid_resolution=(50, 50))
    net = builder.build_network()
    solver = IRDropSolver(net)
    res = solver.solve(v_nom=1.0, total_current=0.4, distribution="uniform")

    # Set limit right below max drop so only 1 cell violates
    max_drop = float(np.max(res.composite_ir_drop_v)) * 1000.0
    analyzer = IRDropAnalyzer(res, delta_v_limit_mv=max_drop - 0.05, max_violation_rows=100)
    analysis = analyzer.analyze()

    assert analysis.violating_node_count >= 1
    assert len(analysis.hotspots) >= 1
    # Worst node must be inside the hotspot
    assert analysis.hotspots[0].max_drop_mv == pytest.approx(analysis.delta_v_max_mv, abs=0.01)
