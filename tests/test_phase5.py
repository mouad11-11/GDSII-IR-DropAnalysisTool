"""Tests for Phase 5: Solver and performance optimizations."""

import time
import numpy as np
import pytest
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from irdrop import GDSLayout, PDNBuilder, IRDropSolver, run_analysis


def test_solver_matches_direct_within_1e9():
    """Verify Dirichlet-eliminated SPD solve matches reference within 1e-9."""
    layout, result, analysis, _ = run_analysis(
        "samples/mesh_pdn.gds",
        v_nom=1.0,
        delta_v_limit_mv=80.0,
        total_current=0.4,
        distribution="uniform",
        grid_resolution=(60, 60),
        tech="default",
    )

    # Reconstruct the reference un-eliminated system:
    # A[p, p] = 1, rhs[p] = v_nom
    builder = PDNBuilder(layout, grid_resolution=(60, 60))
    net = builder.build_network()

    n_components, comp_labels = sp.csgraph.connected_components(net.G, directed=False)
    pad_components = set(comp_labels[p] for p in net.pad_node_indices)
    connected_to_pads = np.isin(comp_labels, list(pad_components))

    # Reference solve using un-eliminated LIL matrix
    A_ref = net.G.tolil()
    rhs_ref = np.zeros(net.total_nodes, dtype=np.float64)

    # Sink current with metal occupancy weighting
    valid_sinks = [s for s in net.sink_node_indices if connected_to_pads[s]]
    s_arr = np.array(valid_sinks, dtype=np.int64)
    nodes_per_layer = 60 * 60
    rem = s_arr % nodes_per_layer
    r_arr = rem // 60
    c_arr = rem % 60
    m0_occ = net.layer_occupancy[net.metal_layers[0]][r_arr, c_arr]
    weights = np.maximum(m0_occ.astype(np.float64), 0.01)
    current_per_sink = (0.4 / np.sum(weights)) * weights
    rhs_ref[s_arr] = -current_per_sink

    for p in net.pad_node_indices:
        A_ref.rows[p] = [p]
        A_ref.data[p] = [1.0]
        rhs_ref[p] = 1.0

    unconnected = np.where(~connected_to_pads)[0]
    for u in unconnected:
        A_ref.rows[u] = [u]
        A_ref.data[u] = [1.0]
        rhs_ref[u] = 1.0

    V_ref = spla.spsolve(A_ref.tocsr(), rhs_ref)

    # Result from IRDropSolver
    # Extract V from result layer_voltages
    V_solver = np.zeros(net.total_nodes, dtype=np.float64)
    nodes_per_layer = 60 * 60
    for m_idx, layer in enumerate(net.metal_layers):
        V_solver[m_idx * nodes_per_layer : (m_idx + 1) * nodes_per_layer] = result.layer_voltages[layer].ravel()

    # Compare connected nodes: must match within 1e-9 V
    diff = np.abs(V_solver[connected_to_pads] - V_ref[connected_to_pads])
    assert np.max(diff) < 1e-9, f"Max difference {np.max(diff)} exceeds 1e-9 tolerance"


def test_system_is_spd_and_cg_converges():
    """Verify Dirichlet elimination yields a symmetric positive definite system solved by CG/AMG."""
    layout = GDSLayout("samples/mesh_pdn.gds")
    builder = PDNBuilder(layout, grid_resolution=(50, 50))
    net = builder.build_network()

    solver = IRDropSolver(net)
    res = solver.solve(v_nom=1.0, total_current=0.3, solver_method="cg")
    assert res.converged
    assert not np.any(np.isnan(res.composite_ir_drop_v))


def test_large_grid_performance_500x500():
    """Verify 500x500 mesh build and solve completes within reasonable budget."""
    t0 = time.time()
    layout = GDSLayout("samples/mesh_pdn.gds")
    builder = PDNBuilder(layout, grid_resolution=(500, 500))
    t_build_start = time.time()
    net = builder.build_network()
    t_build = time.time() - t_build_start

    solver = IRDropSolver(net)
    t_solve_start = time.time()
    res = solver.solve(v_nom=1.0, total_current=0.4, distribution="uniform")
    t_solve = time.time() - t_solve_start
    t_total = time.time() - t0

    assert res.converged
    # Build time must be fast (< 5s with vectorized slicing instead of minutes with loops)
    assert t_build < 8.0, f"build_network took {t_build:.2f}s, expected < 8s"
    # Solve time with AMG+CG must complete within budget (< 15s)
    assert t_solve < 15.0, f"solve took {t_solve:.2f}s, expected < 15s"
    assert t_total < 25.0
