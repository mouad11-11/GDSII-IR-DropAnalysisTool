"""Sparse linear solver for PDN node potentials and static IR-drop."""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.ndimage import gaussian_filter

try:
    import pyamg
    HAS_PYAMG = True
except ImportError:
    pyamg = None
    HAS_PYAMG = False

from irdrop.pdn_model import PDNNetwork


@dataclass
class SolverResult:
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
    warnings: List[str] = field(default_factory=list)
    solver_method: str = "direct_spsolve"
    iterations: Optional[int] = None
    residual: Optional[float] = None

    @property
    def grid_resolution(self) -> Tuple[int, int]:
        return (len(self.y_coords_um), len(self.x_coords_um))

    @property
    def cell_width_um(self) -> float:
        if len(self.x_coords_um) > 1:
            return float(self.x_coords_um[1] - self.x_coords_um[0])
        return 0.0

    @property
    def cell_height_um(self) -> float:
        if len(self.y_coords_um) > 1:
            return float(self.y_coords_um[1] - self.y_coords_um[0])
        return 0.0

    @property
    def method_used(self) -> str:
        return self.solver_method


class IRDropSolver:
    def __init__(self, network: PDNNetwork):
        self.network = network

    def solve(
        self,
        v_nom: float = 1.0,
        total_current: float = 0.5,  # Amperes
        distribution: str = "uniform",  # 'uniform', 'center_hotspot', 'dual_hotspot', 'quad_hotspot'
        hotspot_boxes: Optional[List[Dict[str, float]]] = None,
        solver_method: str = "auto",  # 'auto', 'cg', 'amg', 'direct'
        tolerance: float = 1e-10,
        max_iter: int = 500,
    ) -> SolverResult:
        t0 = time.time()
        net = self.network
        ny, nx = net.grid_shape
        min_x, min_y, max_x, max_y = net.bbox

        x_coords = np.linspace(min_x, max_x, nx)
        y_coords = np.linspace(min_y, max_y, ny)
        nodes_per_layer = ny * nx

        # Connected components to isolate floating metal
        n_components, comp_labels = sp.csgraph.connected_components(net.G, directed=False)
        pad_components = set(comp_labels[p] for p in net.pad_node_indices)
        connected_to_pads = np.isin(comp_labels, list(pad_components))

        valid_sink_indices = [s for s in net.sink_node_indices if connected_to_pads[s]]
        if not valid_sink_indices:
            valid_sink_indices = list(net.pad_node_indices)

        s_arr = np.array(valid_sink_indices, dtype=np.int64)
        rem = s_arr % nodes_per_layer
        r_arr = rem // nx
        c_arr = rem % nx
        x_arr = x_coords[c_arr]
        y_arr = y_coords[r_arr]
        weights = np.ones(len(s_arr), dtype=np.float64)

        m0_layer = net.metal_layers[0]
        if m0_layer in net.layer_occupancy:
            m0_occ = net.layer_occupancy[m0_layer][r_arr, c_arr]
            weights *= np.maximum(m0_occ.astype(np.float64), 0.01)

        norm_x = (x_arr - min_x) / (max_x - min_x + 1e-9)
        norm_y = (y_arr - min_y) / (max_y - min_y + 1e-9)

        if distribution == "center_hotspot":
            dist_center_sq = (norm_x - 0.5) ** 2 + (norm_y - 0.5) ** 2
            weights *= (1.0 + 4.0 * np.exp(-dist_center_sq / 0.05))
        elif distribution == "dual_hotspot":
            d1 = (norm_x - 0.3) ** 2 + (norm_y - 0.5) ** 2
            d2 = (norm_x - 0.7) ** 2 + (norm_y - 0.5) ** 2
            weights *= (1.0 + 5.0 * np.exp(-d1 / 0.04) + 5.0 * np.exp(-d2 / 0.04))
        elif distribution == "quad_hotspot":
            d1 = (norm_x - 0.3) ** 2 + (norm_y - 0.3) ** 2
            d2 = (norm_x - 0.7) ** 2 + (norm_y - 0.3) ** 2
            d3 = (norm_x - 0.3) ** 2 + (norm_y - 0.7) ** 2
            d4 = (norm_x - 0.7) ** 2 + (norm_y - 0.7) ** 2
            weights *= (1.0 + 4.0 * (np.exp(-d1 / 0.03) + np.exp(-d2 / 0.03) + np.exp(-d3 / 0.03) + np.exp(-d4 / 0.03)))

        if hotspot_boxes:
            for box in hotspot_boxes:
                in_box = (
                    (x_arr >= box.get("x_min", 0)) & (x_arr <= box.get("x_max", 0)) &
                    (y_arr >= box.get("y_min", 0)) & (y_arr <= box.get("y_max", 0))
                )
                weights[in_box] *= float(box.get("multiplier", 2.0))

        total_weight = float(np.sum(weights))
        if total_weight > 0:
            current_per_sink = (total_current / total_weight) * weights
        else:
            current_per_sink = np.full(len(s_arr), total_current / max(len(s_arr), 1))

        # Setup RHS vector of current injections
        i_inj = np.zeros(net.total_nodes, dtype=np.float64)
        i_inj[s_arr] = -current_per_sink

        pad_indices = np.array(list(set(net.pad_node_indices)), dtype=np.int64)
        is_pad = np.zeros(net.total_nodes, dtype=bool)
        is_pad[pad_indices] = True

        # Unknown nodes: connected to pads, but NOT pad nodes themselves
        unknown_mask = connected_to_pads & (~is_pad)
        unknown_indices = np.where(unknown_mask)[0]

        warnings = list(self.network.warnings)
        converged = True

        if len(unknown_indices) == 0:
            V = np.full(net.total_nodes, v_nom, dtype=np.float64)
        else:
            # Dirichlet elimination into RHS to maintain symmetric positive definite (SPD) system:
            # G_UU * V_U = I_U - G_UD * (v_nom * 1_D)
            G_csr = net.G.tocsr()
            A_UU = G_csr[unknown_indices, :][:, unknown_indices]
            
            # Off-diagonal Dirichlet coupling
            if len(pad_indices) > 0:
                G_UD = G_csr[unknown_indices, :][:, pad_indices]
                dirichlet_coupling = np.squeeze(np.asarray(G_UD.sum(axis=1)))
                b_U = i_inj[unknown_indices] - (v_nom * dirichlet_coupling)
            else:
                b_U = i_inj[unknown_indices]

            n_unknowns = len(unknown_indices)

            # Determine solver strategy
            use_direct = False
            if solver_method == "direct":
                use_direct = True
            elif solver_method == "auto":
                if n_unknowns < 5000 or not HAS_PYAMG:
                    use_direct = True
                else:
                    use_direct = False
            elif solver_method in ("cg", "amg"):
                use_direct = False

            actual_method = "direct_spsolve"
            iter_count: Optional[int] = None

            if use_direct:
                V_U = spla.spsolve(A_UU.tocsc(), b_U)
                actual_method = "direct_spsolve"
            else:
                # Solve via Preconditioned Conjugate Gradient (PCG)
                M = None
                if HAS_PYAMG:
                    try:
                        ml = pyamg.ruge_stuben_solver(A_UU)
                        M = ml.aspreconditioner(cycle='V')
                    except Exception:
                        try:
                            ml = pyamg.smoothed_aggregation_solver(A_UU)
                            M = ml.aspreconditioner(cycle='V')
                        except Exception:
                            M = None

                if M is None:
                    # Jacobi diagonal preconditioner
                    diag = A_UU.diagonal()
                    diag_inv = np.where(diag > 0, 1.0 / diag, 1.0)
                    M = sp.diags(diag_inv, format='csr')
                    actual_method = "cg_jacobi"
                else:
                    actual_method = "amg_cg"

                iters = 0
                def cg_cb(xk):
                    nonlocal iters
                    iters += 1

                V_U, info = spla.cg(A_UU, b_U, M=M, rtol=tolerance, atol=1e-12, maxiter=max_iter, callback=cg_cb)
                iter_count = iters
                if info != 0:
                    V_U = spla.spsolve(A_UU.tocsc(), b_U)
                    actual_method = "direct_spsolve (fallback)"
                    warnings.append(f"Warning: Iterative solver did not reach tolerance (code={info}); used direct fallback.")

            # Residual check
            res_val = float(np.linalg.norm(A_UU.dot(V_U) - b_U))
            b_norm = float(np.linalg.norm(b_U))
            rel_res = res_val / max(b_norm, 1e-12)
            if rel_res > 1e-3:
                warnings.append(f"Blocking: Solver relative residual {rel_res:.2e} exceeded convergence tolerance.")
                converged = False

            # Reconstruct full potentials vector
            V = np.full(net.total_nodes, v_nom, dtype=np.float64)
            V[unknown_indices] = V_U

        solve_time = time.time() - t0

        # Check residual and NaNs
        has_nan = bool(np.any(np.isnan(V)) or np.any(np.isinf(V)))
        if has_nan:
            warnings.append("Blocking: Solver produced NaN or Inf potential values.")
            converged = False

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
            converged=converged,
            layer_voltages=layer_voltages,
            layer_ir_drops=layer_ir_drops,
            layer_active_mask=layer_active_mask,
            composite_ir_drop_v=composite_drop_v,
            smoothed_ir_drop_mv=smooth_map_mv,
            active_die_mask=overall_active_mask,
            x_coords_um=x_coords,
            y_coords_um=y_coords,
            warnings=warnings,
            solver_method=actual_method,
            iterations=iter_count,
            residual=rel_res if 'rel_res' in locals() else None,
        )

