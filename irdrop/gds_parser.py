"""GDSII parser, hierarchy flattener, and 2D grid rasterizer."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import cv2
import gdstk
import numpy as np

from irdrop.tech import TechConfig, load_tech_file


@dataclass
class LayerInfo:
    layer: int
    datatype: int = 0
    name: str = ""
    polygon_count: int = 0
    total_area: float = 0.0
    bbox: Optional[Tuple[float, float, float, float]] = None
    role: str = "metal"  # 'metal', 'via', 'pad', 'boundary', 'ignore', 'pin', 'label'
    sheet_resistance: float = 0.05  # Ohm/sq for metals
    via_resistance: float = 1.5     # Ohm per via for via layers
    metal_index: int = 0            # Z-order / vertical stack index for metals (0=M1, 1=M2...)
    connects: Optional[List[int]] = None


class GDSLayout:
    def __init__(
        self,
        filepath: str,
        top_cell_name: Optional[str] = None,
        tech: Optional[Union[str, TechConfig]] = "default",
        guess_layers: bool = False,
    ):
        self.filepath = str(Path(filepath).resolve())
        self.raw_lib = gdstk.read_gds(self.filepath)
        self.unit = self.raw_lib.unit
        self.precision = self.raw_lib.precision
        
        # Load tech configuration
        if isinstance(tech, str):
            self.tech: Optional[TechConfig] = load_tech_file(tech)
        elif isinstance(tech, TechConfig):
            self.tech = tech
        else:
            self.tech = None

        self.guess_layers = guess_layers
        self.warnings: List[str] = []

        # Determine cell hierarchy
        self.cell_names = [c.name for c in self.raw_lib.cells]
        self.top_cell = self._find_top_cell(top_cell_name)
        
        # Flattened polygons and labels
        self.polygons_by_key: Dict[Tuple[int, int], List[np.ndarray]] = {}
        self.polygons_by_layer: Dict[int, List[np.ndarray]] = {}
        self.labels: List[Dict[str, Any]] = []
        self.pad_labels: List[Dict[str, Any]] = []
        self.layers_by_key: Dict[Tuple[int, int], LayerInfo] = {}
        self.layers: Dict[int, LayerInfo] = {}
        self.bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        
        self._extract_geometry()
        self._classify_layers()

    def get_layer_info(self, layer: int, datatype: int = 0) -> Optional[LayerInfo]:
        """Returns layer metadata for a (layer, datatype) pair or layer."""
        if (layer, datatype) in self.layers_by_key:
            return self.layers_by_key[(layer, datatype)]
        return self.layers.get(layer)

    def _find_top_cell(self, preferred_name: Optional[str] = None) -> gdstk.Cell:
        if not self.raw_lib.cells:
            raise ValueError(f"No cells found in GDS file: {self.filepath}")
            
        if preferred_name:
            for cell in self.raw_lib.cells:
                if cell.name == preferred_name:
                    return cell

        # Find cells that are not referenced by any other cell
        referenced = set()
        for cell in self.raw_lib.cells:
            for ref in cell.references:
                if isinstance(ref.cell, gdstk.Cell):
                    referenced.add(ref.cell.name)
                elif isinstance(ref.cell, str):
                    referenced.add(ref.cell)
                    
        top_candidates = [c for c in self.raw_lib.cells if c.name not in referenced]
        if top_candidates:
            # Pick the candidate with the most elements or largest area
            return max(top_candidates, key=lambda c: len(c.polygons) + len(c.references) + len(c.paths))
        
        # Default to first cell
        return self.raw_lib.cells[0]

    def _extract_geometry(self):
        # Create a copy and flatten all cell references
        flat_cell = self.top_cell.copy(f"{self.top_cell.name}_FLAT", deep_copy=True)
        flat_cell.flatten()
        
        # Collect polygons from polygons and paths with (layer, datatype)
        all_raw_polys: List[Tuple[int, int, np.ndarray]] = []
        for poly in flat_cell.polygons:
            all_raw_polys.append((poly.layer, poly.datatype, poly.points))
        for path in flat_cell.paths:
            for poly in path.to_polygons():
                all_raw_polys.append((poly.layer, poly.datatype, poly.points))

        scale_to_um = float(self.unit / 1e-6)

        # Collect bounding box
        cell_bb = flat_cell.bounding_box()
        if cell_bb is not None:
            self.bbox = (
                float(cell_bb[0][0]) * scale_to_um,
                float(cell_bb[0][1]) * scale_to_um,
                float(cell_bb[1][0]) * scale_to_um,
                float(cell_bb[1][1]) * scale_to_um,
            )
        else:
            self.bbox = (0.0, 0.0, 100.0, 100.0)

        # Stats tracking for (layer, datatype) and layer
        key_stats: Dict[Tuple[int, int], Dict[str, Any]] = {}
        layer_stats: Dict[int, Dict[str, Any]] = {}

        for layer, datatype, raw_points in all_raw_polys:
            # Skip pin, label, and filler datatypes per protocol:
            # Check tech configuration role or standard EDA pin/label datatypes (2, 25)
            if self.tech:
                tech_l = self.tech.get_layer(layer, datatype)
                if tech_l and tech_l.role in ("pin", "label", "filler", "ignore"):
                    continue
            if datatype in (2, 25):
                continue

            points = raw_points.astype(np.float64) * scale_to_um  # (N, 2) in um
            key = (layer, datatype)

            if key not in self.polygons_by_key:
                self.polygons_by_key[key] = []
                key_stats[key] = {
                    "count": 0,
                    "area": 0.0,
                    "min_x": float("inf"),
                    "min_y": float("inf"),
                    "max_x": float("-inf"),
                    "max_y": float("-inf"),
                }
            self.polygons_by_key[key].append(points)

            if layer not in self.polygons_by_layer:
                self.polygons_by_layer[layer] = []
                layer_stats[layer] = {
                    "count": 0,
                    "area": 0.0,
                    "min_x": float("inf"),
                    "min_y": float("inf"),
                    "max_x": float("-inf"),
                    "max_y": float("-inf"),
                }
            self.polygons_by_layer[layer].append(points)

            # Area computation (shoelace formula)
            x, y = points[:, 0], points[:, 1]
            area = float(0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))
            
            p_min_x, p_min_y = float(np.min(x)), float(np.min(y))
            p_max_x, p_max_y = float(np.max(x)), float(np.max(y))

            for stats in (key_stats[key], layer_stats[layer]):
                stats["area"] += area
                stats["count"] += 1
                stats["min_x"] = min(stats["min_x"], p_min_x)
                stats["min_y"] = min(stats["min_y"], p_min_y)
                stats["max_x"] = max(stats["max_x"], p_max_x)
                stats["max_y"] = max(stats["max_y"], p_max_y)

        # Extract labels and pad labels
        pad_regex = re.compile(r'(?:^|[^A-Za-z0-9])(VDD|VCC|PWR|POWER|C4|PAD)(?:$|[^A-Za-z0-9])', re.IGNORECASE)
        for label in flat_cell.labels:
            lbl_dict = {
                "text": label.text,
                "x": float(label.origin[0]) * scale_to_um,
                "y": float(label.origin[1]) * scale_to_um,
                "layer": label.layer,
                "texttype": label.texttype,
            }
            self.labels.append(lbl_dict)
            if pad_regex.search(label.text):
                self.pad_labels.append(lbl_dict)

        # Initialize LayerInfo objects for (layer, datatype)
        for (layer, datatype), stats in key_stats.items():
            bbox = (stats["min_x"], stats["min_y"], stats["max_x"], stats["max_y"])
            self.layers_by_key[(layer, datatype)] = LayerInfo(
                layer=layer,
                datatype=datatype,
                polygon_count=stats["count"],
                total_area=stats["area"],
                bbox=bbox,
            )

        # Initialize LayerInfo objects for layer
        for layer, stats in layer_stats.items():
            bbox = (stats["min_x"], stats["min_y"], stats["max_x"], stats["max_y"])
            self.layers[layer] = LayerInfo(
                layer=layer,
                datatype=0,
                polygon_count=stats["count"],
                total_area=stats["area"],
                bbox=bbox,
            )

    def _classify_single_heuristic(self, info: LayerInfo, metal_idx: int = 0) -> int:
        layer = info.layer
        if layer >= 60:
            info.role = "boundary"
            info.name = f"Boundary (L{layer})"
        elif layer == 10:
            info.role = "pad"
            info.name = f"PAD / C4 (L{layer})"
        elif layer % 2 == 0 and layer < 10:
            info.role = "via"
            info.name = f"Via {layer // 2} (L{layer})"
            info.via_resistance = 1.5
            info.connects = [layer - 1, layer + 1]
        else:
            info.role = "metal"
            info.metal_index = metal_idx
            info.name = f"Metal {metal_idx + 1} (L{layer})"
            info.sheet_resistance = max(0.015, 0.12 / (1 + 0.6 * metal_idx))
            return metal_idx + 1
        return metal_idx

    def _classify_layers(self):
        """
        Classifies layers into metal, via, pad, boundary, or ignore based on
        technology file or explicit --guess-layers heuristic.
        """
        if self.tech:
            # Map layers via tech config
            for (l, dt), info in self.layers_by_key.items():
                t_layer = self.tech.get_layer(l, dt) or self.tech.get_layer(l, 0)
                if t_layer:
                    info.role = t_layer.role
                    info.name = t_layer.name
                    info.sheet_resistance = t_layer.sheet_resistance
                    info.via_resistance = t_layer.via_resistance
                    info.metal_index = t_layer.stack_order if t_layer.stack_order is not None else 0
                    info.connects = t_layer.connects
                else:
                    if self.guess_layers:
                        self.warnings.append(
                            f"Layer ({l}, {dt}) not defined in tech '{self.tech.name}'; guessing role via heuristic."
                        )
                        self._classify_single_heuristic(info)
                    else:
                        info.role = "ignore"
                        info.name = f"L{l}D{dt}"

            # Sync layer-level metadata
            for l, info in self.layers.items():
                if (l, 0) in self.layers_by_key:
                    k_info = self.layers_by_key[(l, 0)]
                else:
                    matching = [k for (layer_k, _), k in self.layers_by_key.items() if layer_k == l]
                    k_info = matching[0] if matching else None

                if k_info:
                    info.role = k_info.role
                    info.name = k_info.name
                    info.sheet_resistance = k_info.sheet_resistance
                    info.via_resistance = k_info.via_resistance
                    info.metal_index = k_info.metal_index
                    info.connects = k_info.connects
                else:
                    t_layer = self.tech.get_layer(l, 0)
                    if t_layer:
                        info.role = t_layer.role
                        info.name = t_layer.name
                        info.sheet_resistance = t_layer.sheet_resistance
                        info.via_resistance = t_layer.via_resistance
                        info.metal_index = t_layer.stack_order if t_layer.stack_order is not None else 0
                        info.connects = t_layer.connects
                    elif self.guess_layers:
                        self.warnings.append(
                            f"Layer {l} not defined in tech '{self.tech.name}'; guessing role via heuristic."
                        )
                        self._classify_single_heuristic(info)
                    else:
                        info.role = "ignore"
                        info.name = f"L{l}"
        else:
            if not self.guess_layers:
                raise ValueError("No tech configuration provided. Specify a tech file or use --guess-layers.")
            self.warnings.append("Warning: guessing layer roles using heuristics (--guess-layers).")
            m_idx = 0
            for l in sorted(self.layers.keys()):
                info = self.layers[l]
                m_idx = self._classify_single_heuristic(info, m_idx)
            for (l, dt), info in self.layers_by_key.items():
                if l in self.layers:
                    l_info = self.layers[l]
                    info.role = l_info.role
                    info.name = l_info.name
                    info.sheet_resistance = l_info.sheet_resistance
                    info.via_resistance = l_info.via_resistance
                    info.metal_index = l_info.metal_index
                    info.connects = l_info.connects

    def get_via_counts(self, layer: int, grid_shape: Tuple[int, int]) -> np.ndarray:
        """
        Counts discrete via polygon cuts per grid cell.
        Returns a 2D float32 array of shape (ny, nx) with via cut counts in each cell.
        """
        ny, nx = grid_shape
        counts = np.zeros((ny, nx), dtype=np.float32)
        polys = self.polygons_by_layer.get(layer, [])
        if not polys:
            return counts

        min_x, min_y, max_x, max_y = self.bbox
        width = max(max_x - min_x, 1e-6)
        height = max(max_y - min_y, 1e-6)
        dx = width / max(nx - 1, 1)
        dy = height / max(ny - 1, 1)

        for poly in polys:
            cx = float(np.mean(poly[:, 0]))
            cy = float(np.mean(poly[:, 1]))
            c = int(np.clip((cx - min_x) / dx, 0, nx - 1))
            r = int(np.clip((cy - min_y) / dy, 0, ny - 1))
            counts[r, c] += 1.0

        return counts

    def rasterize_layer(self, layer: int, grid_shape: Tuple[int, int]) -> np.ndarray:
        """
        Rasterizes polygons on a specific layer into a 2D float occupancy grid of shape (ny, nx).
        Value at (r, c) is between 0.0 and 1.0 (binary coverage or fraction).
        """
        ny, nx = grid_shape
        if layer not in self.polygons_by_layer or not self.polygons_by_layer[layer]:
            return np.zeros((ny, nx), dtype=np.float32)

        min_x, min_y, max_x, max_y = self.bbox
        width = max(max_x - min_x, 1e-6)
        height = max(max_y - min_y, 1e-6)

        # Scale factor from layout (microns) to raster pixels
        scale_x = (nx - 1) / width
        scale_y = (ny - 1) / height

        # Create blank image for OpenCV
        canvas = np.zeros((ny, nx), dtype=np.uint8)

        polys = self.polygons_by_layer[layer]
        cv_pts_list = []
        for poly in polys:
            # Transform: x -> col, y -> row
            px = np.clip((poly[:, 0] - min_x) * scale_x, 0, nx - 1)
            py = np.clip((poly[:, 1] - min_y) * scale_y, 0, ny - 1)
            pts = np.column_stack((px, py)).astype(np.int32)
            cv_pts_list.append(pts)

        if cv_pts_list:
            cv2.fillPoly(canvas, cv_pts_list, color=255)
            # Ensure sub-pixel thin wire boundaries and vias are preserved
            cv2.polylines(canvas, cv_pts_list, isClosed=True, color=255, thickness=1)

        # Normalize to 0.0 - 1.0
        return (canvas / 255.0).astype(np.float32)

    def get_summary(self) -> Dict[str, Any]:
        """Returns JSON-serializable layout metadata."""
        min_x, min_y, max_x, max_y = self.bbox
        return {
            "filepath": self.filepath,
            "filename": Path(self.filepath).name,
            "top_cell": self.top_cell.name,
            "all_cells": self.cell_names,
            "bbox_microns": {
                "min_x": round(min_x, 3),
                "min_y": round(min_y, 3),
                "max_x": round(max_x, 3),
                "max_y": round(max_y, 3),
                "width": round(max_x - min_x, 3),
                "height": round(max_y - min_y, 3),
            },
            "layers": [
                {
                    "layer": info.layer,
                    "datatype": info.datatype,
                    "name": info.name,
                    "role": info.role,
                    "polygon_count": info.polygon_count,
                    "total_area_um2": round(info.total_area, 2),
                    "sheet_resistance": info.sheet_resistance,
                    "via_resistance": info.via_resistance,
                    "metal_index": info.metal_index,
                }
                for info in sorted(self.layers.values(), key=lambda l: l.layer)
            ],
            "labels": self.labels,
            "pad_labels": self.pad_labels,
            "warnings": self.warnings,
        }
