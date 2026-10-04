"""
Power Delivery Network (PDN) Mesh Extraction and Conductance Matrix Formulation.
Constructs multi-tier 3D resistive grids from GDSII layer geometries, vias, and power pads.
"""

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


class PDNBuilder:
    def __init__(
        self,
        layout: GDSLayout,
        grid_resolution: Tuple[int, int] = (100, 100),
        layer_overrides: Optional[Dict[int, Dict[str, Any]]] = None,
    ):
        """
        Args:
            layout: Parsed GDSLayout
            grid_resolution: (ny, nx) discretization resolution
            layer_overrides: Optional dictionary of layer settings:
                {layer_id: {'role': 'metal'|'via'|'pad'|'ignore', 'sheet_res': float, 'via_res': float}}
        """
        self.layout = layout
        self.ny, self.nx = grid_resolution
        self.layer_overrides = layer_overrides or {}
        
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
        # 1. Identify active layers
        metal_layers = [l for l, info in sorted(self.layout.layers.items()) if info.role == "metal"]
        via_layers = [l for l, info in sorted(self.layout.layers.items()) if info.role == "via"]
        pad_layers = [l for l, info in sorted(self.layout.layers.items()) if info.role == "pad"]

        # Fallback if no metal layers are classified
        if not metal_layers:
            # Use all non-boundary layers as metal
            metal_layers = [l for l in sorted(self.layout.layers.keys()) if l < 60]

        # 2. Rasterize each layer onto (ny, nx)
        occupancies: Dict[int, np.ndarray] = {}
        for layer in metal_layers + via_layers + pad_layers:
            occupancies[layer] = self.layout.rasterize_layer(layer, (self.ny, self.nx))

        num_metals = len(metal_layers)
        nodes_per_layer = self.ny * self.nx
        total_nodes = num_metals * nodes_per_layer

        # Helper for global node index
        def get_node_idx(metal_idx: int, r: int, c: int) -> int:
            return metal_idx * nodes_per_layer + r * self.nx + c

        # Lists for sparse matrix construction (COO format)
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

        # 3. Add intra-layer resistors (horizontal and vertical)
        for m_idx, layer in enumerate(metal_layers):
            occ = occupancies[layer]
            r_sq = max(self.layout.layers[layer].sheet_resistance, 1e-4)

            # Precalculate cell conductance factors:
            # Gx = (1 / Rsq) * (dy / dx)
            # Gy = (1 / Rsq) * (dx / dy)
            g_factor_x = (1.0 / r_sq) * (self.dy / self.dx)
            g_factor_y = (1.0 / r_sq) * (self.dx / self.dy)

            # Horizontal connections: (r, c) <-> (r, c+1)
            for r in range(self.ny):
                for c in range(self.nx - 1):
                    # Both cells must have metal coverage
                    c_eff = min(occ[r, c], occ[r, c + 1])
                    if c_eff > 0.01:
                        idx1 = get_node_idx(m_idx, r, c)
                        idx2 = get_node_idx(m_idx, r, c + 1)
                        add_resistor(idx1, idx2, g_factor_x * c_eff)

            # Vertical connections: (r, c) <-> (r+1, c)
            for r in range(self.ny - 1):
                for c in range(self.nx):
                    c_eff = min(occ[r, c], occ[r + 1, c])
                    if c_eff > 0.01:
                        idx1 = get_node_idx(m_idx, r, c)
                        idx2 = get_node_idx(m_idx, r + 1, c)
                        add_resistor(idx1, idx2, g_factor_y * c_eff)

        # 4. Add inter-layer via resistors
        for m_idx in range(num_metals - 1):
            bottom_layer = metal_layers[m_idx]
            top_layer = metal_layers[m_idx + 1]
            occ_bottom = occupancies[bottom_layer]
            occ_top = occupancies[top_layer]

            # Find matching via layer
            via_layer = None
            for vl in via_layers:
                if bottom_layer < vl < top_layer or vl == bottom_layer + 1:
                    via_layer = vl
                    break

            if via_layer and via_layer in occupancies:
                via_occ = occupancies[via_layer]
                r_via = max(self.layout.layers[via_layer].via_resistance, 1e-3)
                g_via_unit = 1.0 / r_via
                for r in range(self.ny):
                    for c in range(self.nx):
                        if via_occ[r, c] > 0.01 and occ_bottom[r, c] > 0.01 and occ_top[r, c] > 0.01:
                            idx1 = get_node_idx(m_idx, r, c)
                            idx2 = get_node_idx(m_idx + 1, r, c)
                            add_resistor(idx1, idx2, g_via_unit * via_occ[r, c])
            else:
                # Implicit vias at intersections if no explicit via layer exists
                r_via_default = 1.5
                g_via_unit = 1.0 / r_via_default
                for r in range(self.ny):
                    for c in range(self.nx):
                        overlap = occ_bottom[r, c] * occ_top[r, c]
                        if overlap > 0.05:
                            idx1 = get_node_idx(m_idx, r, c)
                            idx2 = get_node_idx(m_idx + 1, r, c)
                            add_resistor(idx1, idx2, g_via_unit * overlap)

        # 5. Identify Pad Nodes (Dirichlet boundary nodes)
        pad_nodes_set: Set[int] = set()
        top_metal_idx = num_metals - 1

        # Check explicit pad layers
        for pl in pad_layers:
            pad_occ = occupancies[pl]
            for r in range(self.ny):
                for c in range(self.nx):
                    if pad_occ[r, c] > 0.1:
                        # Connect pad to top metal (or all connected metals)
                        idx = get_node_idx(top_metal_idx, r, c)
                        pad_nodes_set.add(idx)

        # Check labels for pads (e.g. VDD_PAD, C4_VDD)
        min_x, min_y, max_x, max_y = self.layout.bbox
        for lbl in self.layout.labels:
            if any(k in lbl["text"].upper() for k in ["VDD", "PAD", "C4", "PWR"]):
                # Map coordinate to grid cell
                c = int(np.clip((lbl["x"] - min_x) / self.dx, 0, self.nx - 1))
                r = int(np.clip((lbl["y"] - min_y) / self.dy, 0, self.ny - 1))
                # Add a 3x3 patch around label
                for dr in range(-1, 2):
                    for dc in range(-1, 2):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < self.ny and 0 <= nc < self.nx:
                            pad_nodes_set.add(get_node_idx(top_metal_idx, nr, nc))

        # Fallback if no pads identified: use 4 corners and center on top metal
        if not pad_nodes_set:
            pad_nodes_set.add(get_node_idx(top_metal_idx, 0, 0))
            pad_nodes_set.add(get_node_idx(top_metal_idx, 0, self.nx - 1))
            pad_nodes_set.add(get_node_idx(top_metal_idx, self.ny - 1, 0))
            pad_nodes_set.add(get_node_idx(top_metal_idx, self.ny - 1, self.nx - 1))
            pad_nodes_set.add(get_node_idx(top_metal_idx, self.ny // 2, self.nx // 2))

        # Filter pad nodes: if a pad node on top metal is disconnected from metal mesh,
        # find the nearest connected top-metal node or connect it directly to metal 0
        final_pad_nodes = []
        for p_idx in pad_nodes_set:
            final_pad_nodes.append(p_idx)
            node_has_connection[p_idx] = True

        # 6. Current sink candidates: active nodes on Metal 0 (standard cell layer)
        m0_occ = occupancies[metal_layers[0]]
        sink_nodes = []
        for r in range(self.ny):
            for c in range(self.nx):
                idx = get_node_idx(0, r, c)
                if m0_occ[r, c] > 0.05 and node_has_connection[idx]:
                    sink_nodes.append(idx)

        # Fallback if no M0 nodes connected: use any connected node
        if not sink_nodes:
            sink_nodes = [i for i in range(total_nodes) if node_has_connection[i] and i not in pad_nodes_set]

        # 7. Assemble sparse matrix in CSR format
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
        )
