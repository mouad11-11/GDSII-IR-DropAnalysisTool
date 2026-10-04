"""
Sparse Linear Circuit Solver for Integrated Circuit Power Delivery Networks.
Calculates node voltages, static IR drops, and spatial power dissipation.
"""

from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.ndimage import gaussian_filter

from irdrop.pdn_model import PDNNetwork


@dataclass
class SolverResult:
    """Contains raw and shaped IR drop simulation results."""
    v_nom: float
    total_current: float
    solve_time_seconds: float
    converged: bool
    
    # 2D Grids: shape (ny, nx)
    layer_voltages: Dict[int, np.ndarray]  # metal_layer -> V(r, c)
    layer_ir_drops: Dict[int, np.ndarray]  # metal_layer -> delta_V(r, c) in Volts
    layer_active_mask: Dict[int, np.ndarray]  # metal_layer -> bool mask of metal cells
    
    composite_ir_drop_v: np.ndarray  # worst-case IR drop across all layers (Volts)
    smoothed_ir_drop_mv: np.ndarray  # interpolated continuous 2D heatmap (mV)
    active_die_mask: np.ndarray     # True where chip circuitry exists
    
    # Coordinate grids in micrometers
    x_coords_um: np.ndarray  # (nx,)
    y_coords_um: np.ndarray  # (ny,)


class IRDropSolver:
    def __init__(self, network: PDNNetwork):
        self.network = network

    def solve(
        self,
        v_nom: float = 1.0,
        total_current: float = 0.5,  # Amperes
        distribution: str = "uniform",  # 'uniform', 'center_hotspot', 'dual_hotspot', 'quad_hotspot'
        hotspot_boxes: Optional[List[Dict[str, float]]] = None,  # [{'x_min', 'y_min', 'x_max', 'y_max', 'multiplier'}]
    ) -> SolverResult:
        """
        Solves G * V = I for node voltages across all metal layers.
        """
        t0 = time.time()
        net = self.network
        ny, nx = net.grid_shape
        min_x, min_y, max_x, max_y = net.bbox

        # Build coordinate arrays
        x_coords = np.linspace(min_x, max_x, nx)
        y_coords = np.linspace(min_y, max_y, ny)

        # 1. Identify nodes connected to power pad network
        n_components, comp_labels = sp.csgraph.connected_components(net.G, directed=False)
        pad_components = set(comp_labels[p] for p in net.pad_node_indices)
        connected_to_pads = np.isin(comp_labels, list(pad_components))

        valid_sink_indices = [s for s in net.sink_node_indices if connected_to_pads[s]]
        if not valid_sink_indices:
            valid_sink_indices = list(net.pad_node_indices)

        # Compute current distribution weights for valid sink nodes on M1
        sink_weights = np.zeros(len(valid_sink_indices), dtype=np.float64)
        nodes_per_layer = ny * nx

        for i, s_idx in enumerate(valid_sink_indices):
            rem = s_idx % nodes_per_layer
            r = rem // nx
            c = rem % nx
            x = x_coords[c]
            y = y_coords[r]

            weight = 1.0
            norm_x = (x - min_x) / (max_x - min_x + 1e-9)
            norm_y = (y - min_y) / (max_y - min_y + 1e-9)

            if distribution == "center_hotspot":
                dist_center_sq = (norm_x - 0.5) ** 2 + (norm_y - 0.5) ** 2
                weight = 1.0 + 4.0 * np.exp(-dist_center_sq / 0.05)
            elif distribution == "dual_hotspot":
                d1 = (norm_x - 0.3) ** 2 + (norm_y - 0.5) ** 2
                d2 = (norm_x - 0.7) ** 2 + (norm_y - 0.5) ** 2
                weight = 1.0 + 5.0 * np.exp(-d1 / 0.04) + 5.0 * np.exp(-d2 / 0.04)
            elif distribution == "quad_hotspot":
                d1 = (norm_x - 0.3) ** 2 + (norm_y - 0.3) ** 2
                d2 = (norm_x - 0.7) ** 2 + (norm_y - 0.3) ** 2
                d3 = (norm_x - 0.3) ** 2 + (norm_y - 0.7) ** 2
                d4 = (norm_x - 0.7) ** 2 + (norm_y - 0.7) ** 2
                weight = 1.0 + 4.0 * (np.exp(-d1 / 0.03) + np.exp(-d2 / 0.03) + np.exp(-d3 / 0.03) + np.exp(-d4 / 0.03))

            if hotspot_boxes:
                for box in hotspot_boxes:
                    if (box.get("x_min", 0) <= x <= box.get("x_max", 0)) and (box.get("y_min", 0) <= y <= box.get("y_max", 0)):
                        weight *= float(box.get("multiplier", 2.0))

            sink_weights[i] = weight

        total_weight = np.sum(sink_weights)
        if total_weight > 0:
            current_per_sink = (total_current / total_weight) * sink_weights
        else:
            current_per_sink = np.full(len(valid_sink_indices), total_current / max(len(valid_sink_indices), 1))

        # 2. Setup RHS vector
        rhs = np.zeros(net.total_nodes, dtype=np.float64)
        for i, s_idx in enumerate(valid_sink_indices):
            rhs[s_idx] = -current_per_sink[i]

        # 3. Impose Dirichlet Boundary Conditions on Pad Nodes
        A = net.G.tolil()
        pad_set = set(net.pad_node_indices)

        for p in pad_set:
            A.rows[p] = [p]
            A.data[p] = [1.0]
            rhs[p] = v_nom

        unconnected_nodes = np.where(~connected_to_pads)[0]
        for u in unconnected_nodes:
            A.rows[u] = [u]
            A.data[u] = [1.0]
            rhs[u] = v_nom

        A_csr = A.tocsr()

        # 4. Solve sparse linear system
        V = spla.spsolve(A_csr, rhs)
        solve_time = time.time() - t0

        # 5. Extract layer grids
        layer_voltages: Dict[int, np.ndarray] = {}
        layer_ir_drops: Dict[int, np.ndarray] = {}
        layer_active_mask: Dict[int, np.ndarray] = {}

        composite_drop_v = np.zeros((ny, nx), dtype=np.float64)
        overall_active_mask = np.zeros((ny, nx), dtype=bool)

        for m_idx, layer in enumerate(net.metal_layers):
            start = m_idx * nodes_per_layer
            end = start + nodes_per_layer
            v_layer = V[start:end].reshape((ny, nx))
            drop_layer = v_nom - v_layer

            # Active mask for this layer (where metal occupancy > 0.01 and connected to PDN)
            occ = net.layer_occupancy.get(layer, np.zeros((ny, nx)))
            conn_mask = connected_to_pads[start:end].reshape((ny, nx))
            act_mask = (occ > 0.01) & conn_mask

            layer_voltages[layer] = v_layer
            layer_ir_drops[layer] = drop_layer
            layer_active_mask[layer] = act_mask

            # Accumulate composite worst-case IR drop (highest drop on active metal)
            composite_drop_v = np.maximum(composite_drop_v, np.where(act_mask, drop_layer, 0.0))
            overall_active_mask = overall_active_mask | act_mask

        # 6. Generate smooth 2D continuous heatmap (in mV) for full die visualization
        # Invert/fill unrouted regions smoothly using 2D Gaussian diffusion
        drop_mv = composite_drop_v * 1000.0

        # Replace 0 in unrouted spaces by diffusing from metal rails
        diffused_mv = drop_mv.copy()
        mask_float = overall_active_mask.astype(np.float64)

        if np.any(overall_active_mask):
            # Normalized convolution for smooth spatial interpolation
            sigma = max(1.5, min(nx, ny) / 60.0)
            num = gaussian_filter(diffused_mv * mask_float, sigma=sigma)
            den = gaussian_filter(mask_float, sigma=sigma)
            den[den < 1e-4] = 1.0
            smooth_map_mv = num / den
            # Ensure at metal positions the exact drop is preserved
            smooth_map_mv = np.where(overall_active_mask, drop_mv, smooth_map_mv)
        else:
            smooth_map_mv = diffused_mv

        return SolverResult(
            v_nom=v_nom,
            total_current=total_current,
            solve_time_seconds=solve_time,
            converged=True,
            layer_voltages=layer_voltages,
            layer_ir_drops=layer_ir_drops,
            layer_active_mask=layer_active_mask,
            composite_ir_drop_v=composite_drop_v,
            smoothed_ir_drop_mv=smooth_map_mv,
            active_die_mask=overall_active_mask,
            x_coords_um=x_coords,
            y_coords_um=y_coords,
        )
