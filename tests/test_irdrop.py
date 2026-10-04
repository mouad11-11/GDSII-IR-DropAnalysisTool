"""
Unit and Integration Tests for GDSII IR-Drop Analysis & Precise Value Margin.
"""

from pathlib import Path
import pytest
import numpy as np

from irdrop import GDSLayout, IRDropAnalyzer, IRDropSolver, IRDropVisualizer, PDNBuilder, run_analysis
from irdrop.sample_generator import create_bottleneck_pdn, create_hierarchical_pdn, create_mesh_pdn


@pytest.fixture(scope="session")
def sample_gds_files(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("gds_samples")
    mesh_file = str(tmp_dir / "test_mesh.gds")
    bottle_file = str(tmp_dir / "test_bottle.gds")
    
    create_mesh_pdn(mesh_file)
    create_bottleneck_pdn(bottle_file)
    
    return {"mesh": mesh_file, "bottleneck": bottle_file}


def test_gds_parser(sample_gds_files):
    mesh_path = sample_gds_files["mesh"]
    layout = GDSLayout(mesh_path)
    
    assert layout.top_cell is not None
    assert layout.top_cell.name == "MESH_PDN_TOP"
    assert len(layout.layers) >= 4
    
    min_x, min_y, max_x, max_y = layout.bbox
    assert max_x - min_x > 0
    assert max_y - min_y > 0
    
    # Check rasterization
    occ = layout.rasterize_layer(1, (50, 50))
    assert occ.shape == (50, 50)
    assert np.any(occ > 0)


def test_pdn_builder_and_solver(sample_gds_files):
    mesh_path = sample_gds_files["mesh"]
    layout = GDSLayout(mesh_path)
    builder = PDNBuilder(layout, grid_resolution=(50, 50))
    network = builder.build_network()
    
    assert network.total_nodes == 50 * 50 * len(network.metal_layers)
    assert len(network.pad_node_indices) > 0
    assert len(network.sink_node_indices) > 0
    
    solver = IRDropSolver(network)
    result = solver.solve(v_nom=1.0, total_current=0.2, distribution="uniform")
    
    assert result.converged
    assert result.composite_ir_drop_v.shape == (50, 50)
    assert np.max(result.composite_ir_drop_v) > 0.0
    assert np.max(result.composite_ir_drop_v) < 1.0


def test_precise_value_margin_pass(sample_gds_files):
    mesh_path = sample_gds_files["mesh"]
    layout, result, analysis, visualizer = run_analysis(
        mesh_path,
        v_nom=1.0,
        delta_v_limit_mv=50.0,
        total_current=0.2,
        grid_resolution=(50, 50),
    )
    
    assert analysis.is_safe is True
    assert analysis.status == "PASS"
    assert analysis.margin_mv > 0.0
    assert analysis.margin_percentage > 0.0
    assert analysis.delta_v_max_mv < 50.0
    assert analysis.min_observed_voltage_v > analysis.min_allowed_voltage_v
    assert analysis.worst_node["drop_mv"] == analysis.delta_v_max_mv


def test_precise_value_margin_violation(sample_gds_files):
    bottle_path = sample_gds_files["bottleneck"]
    layout, result, analysis, visualizer = run_analysis(
        bottle_path,
        v_nom=1.0,
        delta_v_limit_mv=25.0,  # Strict threshold to trigger violation
        total_current=0.5,
        grid_resolution=(50, 50),
    )
    
    assert analysis.is_safe is False
    assert analysis.status == "VIOLATION"
    assert analysis.margin_mv < 0.0
    assert analysis.margin_percentage < 0.0
    assert analysis.delta_v_max_mv > 25.0
    assert analysis.min_observed_voltage_v < analysis.min_allowed_voltage_v
    assert len(analysis.hotspots) >= 1
    assert analysis.violating_area_percentage > 0.0


def test_visualizer_figures(sample_gds_files):
    mesh_path = sample_gds_files["mesh"]
    layout, result, analysis, visualizer = run_analysis(
        mesh_path,
        v_nom=1.0,
        delta_v_limit_mv=40.0,
        total_current=0.25,
        grid_resolution=(40, 40),
    )
    
    # Test heatmap generation
    fig_heat = visualizer.generate_heatmap_figure(mode="ir_drop")
    b64_heat = visualizer.to_base64_png(fig_heat)
    assert b64_heat.startswith("data:image/png;base64,")
    
    # Test slack map
    fig_slack = visualizer.generate_heatmap_figure(mode="margin_slack")
    b64_slack = visualizer.to_base64_png(fig_slack)
    assert b64_slack.startswith("data:image/png;base64,")
    
    # Test cutline
    fig_cut = visualizer.generate_cutline_figure()
    b64_cut = visualizer.to_base64_png(fig_cut)
    assert b64_cut.startswith("data:image/png;base64,")

    # Test 3D surface
    fig_3d = visualizer.generate_3d_surface_figure()
    b64_3d = visualizer.to_base64_png(fig_3d)
    assert b64_3d.startswith("data:image/png;base64,")

    # Test histogram
    fig_hist = visualizer.generate_histogram_figure()
    b64_hist = visualizer.to_base64_png(fig_hist)
    assert b64_hist.startswith("data:image/png;base64,")

    # Test current flow vector map
    fig_flow = visualizer.generate_current_flow_figure()
    b64_flow = visualizer.to_base64_png(fig_flow)
    assert b64_flow.startswith("data:image/png;base64,")

    # Test budgeting metrics
    assert analysis.max_safe_current_ma > 0.0
    assert analysis.max_safe_power_w > 0.0
    assert analysis.effective_pdn_resistance_ohm > 0.0
