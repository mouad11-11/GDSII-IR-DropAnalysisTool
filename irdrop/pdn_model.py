"""PDN resistive mesh model and conductance matrix builder."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Set
import numpy as np
import scipy.sparse as sp

from irdrop.gds_parser import GDSLayout, LayerInfo


@dataclass
class PDNNetwork:
    """Represents the discretized multi-layer PDN circuit network."""
    G: sp.csr_matrix
    grid_shape: Tuple[int, int]  # (ny, nx)
    bbox: Tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y) in um
    metal_layers: List[int]
    via_layers: List[int]
    pad_layers: List[int]
    pad_node_indices: List[int]
    sink_node_indices: List[int]
    layer_occupancy: Dict[int, np.ndarray]  # layer -> 2D array (ny, nx)
    layer_names: Dict[int, str]
    total_nodes: int
    dx: float  # um per grid cell in x
    dy: float  # um per grid cell in y
    warnings: List[str] = field(default_factory=list)


class PDNBuilder:
    def __init__(
        self,
        layout: GDSLayout,
        grid_resolution: Tuple[int, int] = (100, 100),
        layer_overrides: Optional[Dict[int, Dict[str, Any]]] = None,
        allow_default_pads: bool = False,
    ):
        """
        Args:
            layout: Parsed GDSLayout
            grid_resolution: (ny, nx) discretization resolution
            layer_overrides: Optional dictionary of layer settings:
                {layer_id: {'role': 'metal'|'via'|'pad'|'ignore', 'sheet_res': float, 'via_res': float}}
            allow_default_pads: Whether to allow falling back to default boundary pads if none found
        """
        self.layout = layout
        self.ny, self.nx = grid_resolution
        self.layer_overrides = layer_overrides or {}
        self.allow_default_pads = allow_default_pads
        
        # Apply layer overrides
        for layer, props in self.layer_overrides.items():
            if layer in self.layout.layers:
                info = self.layout.layers[layer]
                if "role" in props:
                    info.role = props["role"]
                if "sheet_res" in props:
                    info.sheet_resistance = float(props["sheet_res"])
                if "via_res" in props:
                    info.via_resistance = float(props["via_res"])

        min_x, min_y, max_x, max_y = self.layout.bbox
        self.width = max(max_x - min_x, 1e-3)
        self.height = max(max_y - min_y, 1e-3)
        self.dx = self.width / max(self.nx - 1, 1)
        self.dy = self.height / max(self.ny - 1, 1)

    def build_network(self) -> PDNNetwork:
        """Constructs the sparse conductance matrix G for the multi-tier PDN."""
        # Identify active layers
        metal_layers = [l for l, info in sorted(self.layout.layers.items()) if info.role == "metal"]
        via_layers = [l for l, info in sorted(self.layout.layers.items()) if info.role == "via"]
        pad_layers = [l for l, info in sorted(self.layout.layers.items()) if info.role == "pad"]

        # Fallback if no metal layers are classified
        if not metal_layers:
            candidates = [l for l, info in sorted(self.layout.layers.items()) if info.role not in ("pad", "boundary") and l < 60]
            if not candidates:
                raise ValueError("No valid current sinks found on active metal rails.")
            metal_layers = candidates

        # Rasterize active layers
        occupancies: Dict[int, np.ndarray] = {}
        for layer in metal_layers + via_layers + pad_layers:
            occupancies[layer] = self.layout.rasterize_layer(layer, (self.ny, self.nx))

        num_metals = len(metal_layers)
        nodes_per_layer = self.ny * self.nx
        total_nodes = num_metals * nodes_per_layer

        def get_node_idx(metal_idx: int, r: int, c: int) -> int:
            return metal_idx * nodes_per_layer + r * self.nx + c

        row_indices = []
        col_indices = []
        conductance_vals = []
        node_has_connection = np.zeros(total_nodes, dtype=bool)

        def add_resistor(idx1: int, idx2: int, g: float):
            if g <= 0.0 or idx1 == idx2:
                return
            row_indices.extend([idx1, idx2, idx1, idx2])
            col_indices.extend([idx1, idx2, idx2, idx1])
            conductance_vals.extend([g, g, -g, -g])
            node_has_connection[idx1] = True
            node_has_connection[idx2] = True

        # Intra-layer metal conductances
        for m_idx, layer in enumerate(metal_layers):
            occ = occupancies[layer]
            r_sq = max(self.layout.layers[layer].sheet_resistance, 1e-4)

            g_factor_x = (1.0 / r_sq) * (self.dy / self.dx)
            g_factor_y = (1.0 / r_sq) * (self.dx / self.dy)

            for r in range(self.ny):
                for c in range(self.nx - 1):
                    c_eff = min(occ[r, c], occ[r, c + 1])
                    if c_eff > 0.01:
                        idx1 = get_node_idx(m_idx, r, c)
                        idx2 = get_node_idx(m_idx, r, c + 1)
                        add_resistor(idx1, idx2, g_factor_x * c_eff)

            for r in range(self.ny - 1):
                for c in range(self.nx):
                    c_eff = min(occ[r, c], occ[r + 1, c])
                    if c_eff > 0.01:
                        idx1 = get_node_idx(m_idx, r, c)
                        idx2 = get_node_idx(m_idx, r + 1, c)
                        add_resistor(idx1, idx2, g_factor_y * c_eff)

        # Inter-layer via conductances
        for m_idx in range(num_metals - 1):
            bottom_layer = metal_layers[m_idx]
            top_layer = metal_layers[m_idx + 1]
            occ_bottom = occupancies[bottom_layer]
            occ_top = occupancies[top_layer]

            via_layer = None
            if self.layout.tech:
                via_tech = self.layout.tech.get_via_between(bottom_layer, top_layer)
                if via_tech:
                    via_layer = via_tech.layer

            if via_layer is None:
                for vl in via_layers:
                    v_info = self.layout.get_layer_info(vl)
                    if v_info and v_info.connects and set(v_info.connects) == {bottom_layer, top_layer}:
                        via_layer = vl
                        break

            if via_layer is None and getattr(self.layout, "guess_layers", False):
                for vl in via_layers:
                    if bottom_layer < vl < top_layer or vl == bottom_layer + 1:
                        via_layer = vl
                        break

            if via_layer and via_layer in occupancies:
                via_counts = self.layout.get_via_counts(via_layer, (self.ny, self.nx))
                r_via = max(self.layout.layers[via_layer].via_resistance, 1e-3)
                g_via_unit = 1.0 / r_via
                for r in range(self.ny):
                    for c in range(self.nx):
                        num_vias = via_counts[r, c]
                        v_eff = num_vias if num_vias > 0 else occupancies[via_layer][r, c]
                        if v_eff > 0.01 and occ_bottom[r, c] > 0.01 and occ_top[r, c] > 0.01:
                            idx1 = get_node_idx(m_idx, r, c)
                            idx2 = get_node_idx(m_idx + 1, r, c)
                            add_resistor(idx1, idx2, g_via_unit * v_eff)
            else:
                r_via_default = 1.5
                g_via_unit = 1.0 / r_via_default
                for r in range(self.ny):
                    for c in range(self.nx):
                        overlap = occ_bottom[r, c] * occ_top[r, c]
                        if overlap > 0.05:
                            idx1 = get_node_idx(m_idx, r, c)
                            idx2 = get_node_idx(m_idx + 1, r, c)
                            add_resistor(idx1, idx2, g_via_unit * overlap)

        warnings: List[str] = list(self.layout.warnings)

        # Power pad boundary nodes
        pad_nodes_set: Set[int] = set()
        top_metal_idx = num_metals - 1

        for pl in pad_layers:
            pad_occ = occupancies[pl]
            for r in range(self.ny):
                for c in range(self.nx):
                    if pad_occ[r, c] > 0.1:
                        idx = get_node_idx(top_metal_idx, r, c)
                        pad_nodes_set.add(idx)

        min_x, min_y, max_x, max_y = self.layout.bbox
        for lbl in self.layout.pad_labels:
            c = int(np.clip((lbl["x"] - min_x) / self.dx, 0, self.nx - 1))
            r = int(np.clip((lbl["y"] - min_y) / self.dy, 0, self.ny - 1))
            lbl_metal_idx = top_metal_idx
            if lbl.get("layer") in metal_layers:
                lbl_metal_idx = metal_layers.index(lbl["layer"])
            for dr in range(-1, 2):
                for dc in range(-1, 2):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < self.ny and 0 <= nc < self.nx:
                        pad_nodes_set.add(get_node_idx(lbl_metal_idx, nr, nc))

        if not pad_nodes_set:
            if not self.allow_default_pads:
                raise ValueError("No power pads or C4 bumps detected in layout. Use --allow-default-pads to inject default peripheral pads.")
            warnings.append("Warning: No power pads detected in layout; invented default peripheral and center pads.")
            pad_nodes_set.add(get_node_idx(top_metal_idx, 0, 0))
            pad_nodes_set.add(get_node_idx(top_metal_idx, 0, self.nx - 1))
            pad_nodes_set.add(get_node_idx(top_metal_idx, self.ny - 1, 0))
            pad_nodes_set.add(get_node_idx(top_metal_idx, self.ny - 1, self.nx - 1))
            pad_nodes_set.add(get_node_idx(top_metal_idx, self.ny // 2, self.nx // 2))

        final_pad_nodes = []
        for p_idx in pad_nodes_set:
            final_pad_nodes.append(p_idx)
            node_has_connection[p_idx] = True

        # Current sinks on standard-cell rail (M1)
        if len(metal_layers) == 0:
            raise ValueError("No valid current sinks found on active metal rails.")
        m0_occ = occupancies[metal_layers[0]]
        sink_nodes = []
        for r in range(self.ny):
            for c in range(self.nx):
                idx = get_node_idx(0, r, c)
                if m0_occ[r, c] > 0.05 and node_has_connection[idx]:
                    sink_nodes.append(idx)

        if not sink_nodes:
            raise ValueError("No valid current sinks found on active metal rails.")

        # Assemble CSR matrix
        if not row_indices:
            # Degenerate case fallback
            row_indices = [0]
            col_indices = [0]
            conductance_vals = [1.0]

        G_coo = sp.coo_matrix(
            (conductance_vals, (row_indices, col_indices)),
            shape=(total_nodes, total_nodes),
            dtype=np.float64,
        )
        G = G_coo.tocsr()

        # Check electrical connectivity of sinks to pads
        n_components, comp_labels = sp.csgraph.connected_components(G, directed=False)
        pad_comps = set(comp_labels[p] for p in final_pad_nodes)
        disconnected_sinks = [s for s in sink_nodes if comp_labels[s] not in pad_comps]
        if disconnected_sinks:
            disc_count = len(disconnected_sinks)
            disc_frac = disc_count / max(len(sink_nodes), 1)
            warnings.append(
                f"Blocking: {disc_count} current sink nodes ({disc_frac * 100:.1f}%) are disconnected from power pads (floating load)."
            )

        # Regularize / isolate unreferenced or disconnected nodes to keep G strictly positive definite
        diag = G.diagonal().copy()
        zero_diag_nodes = np.where(diag <= 1e-12)[0]
        if len(zero_diag_nodes) > 0:
            # Set isolated nodes equation: 1.0 * V = V_nom
            add_diag = np.zeros(total_nodes, dtype=np.float64)
            add_diag[zero_diag_nodes] = 1.0
            G = G + sp.diags(add_diag, shape=(total_nodes, total_nodes), format='csr')

        layer_names = {l: self.layout.layers[l].name for l in metal_layers + via_layers + pad_layers}

        return PDNNetwork(
            G=G,
            grid_shape=(self.ny, self.nx),
            bbox=self.layout.bbox,
            metal_layers=metal_layers,
            via_layers=via_layers,
            pad_layers=pad_layers,
            pad_node_indices=final_pad_nodes,
            sink_node_indices=sink_nodes,
            layer_occupancy=occupancies,
            layer_names=layer_names,
            total_nodes=total_nodes,
            dx=self.dx,
            dy=self.dy,
            warnings=warnings,
        )

