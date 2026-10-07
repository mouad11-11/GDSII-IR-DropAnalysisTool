"""GDSII layout parser and rasterizer."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import cv2
import gdstk
import numpy as np


@dataclass
class LayerInfo:
    layer: int
    datatype: int = 0
    name: str = ""
    polygon_count: int = 0
    total_area: float = 0.0
    bbox: Optional[Tuple[float, float, float, float]] = None
    role: str = "metal"  # 'metal', 'via', 'pad', 'boundary', 'ignore'
    sheet_resistance: float = 0.05  # Ohm/sq for metals
    via_resistance: float = 1.5     # Ohm per via for via layers
    metal_index: int = 0            # Z-order / vertical stack index for metals (0=M1, 1=M2...)


class GDSLayout:
    def __init__(self, filepath: str, top_cell_name: Optional[str] = None):
        self.filepath = str(Path(filepath).resolve())
        self.raw_lib = gdstk.read_gds(self.filepath)
        self.unit = self.raw_lib.unit
        self.precision = self.raw_lib.precision
        
        # Determine cell hierarchy
        self.cell_names = [c.name for c in self.raw_lib.cells]
        self.top_cell = self._find_top_cell(top_cell_name)
        
        # Flattened polygons and labels
        self.polygons_by_layer: Dict[int, List[np.ndarray]] = {}
        self.labels: List[Dict[str, Any]] = []
        self.layers: Dict[int, LayerInfo] = {}
        self.bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        
        self._extract_geometry()
        self._classify_default_layers()

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
        
        # Collect polygons from polygons and paths
        all_polys = list(flat_cell.polygons)
        for path in flat_cell.paths:
            all_polys.extend(path.to_polygons())

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

        # Group by layer
        layer_stats: Dict[int, Dict[str, Any]] = {}
        
        for poly in all_polys:
            layer = poly.layer
            points = poly.points.astype(np.float64) * scale_to_um  # (N, 2) in um
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
            area = 0.5 * np.abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
            layer_stats[layer]["area"] += float(area)
            layer_stats[layer]["count"] += 1
            
            p_min_x, p_min_y = float(np.min(x)), float(np.min(y))
            p_max_x, p_max_y = float(np.max(x)), float(np.max(y))
            layer_stats[layer]["min_x"] = min(layer_stats[layer]["min_x"], p_min_x)
            layer_stats[layer]["min_y"] = min(layer_stats[layer]["min_y"], p_min_y)
            layer_stats[layer]["max_x"] = max(layer_stats[layer]["max_x"], p_max_x)
            layer_stats[layer]["max_y"] = max(layer_stats[layer]["max_y"], p_max_y)

        # Extract labels
        for label in flat_cell.labels:
            self.labels.append({
                "text": label.text,
                "x": float(label.origin[0]) * scale_to_um,
                "y": float(label.origin[1]) * scale_to_um,
                "layer": label.layer,
                "texttype": label.texttype,
            })

        # Initialize LayerInfo objects
        for layer, stats in layer_stats.items():
            bbox = (stats["min_x"], stats["min_y"], stats["max_x"], stats["max_y"])
            self.layers[layer] = LayerInfo(
                layer=layer,
                polygon_count=stats["count"],
                total_area=stats["area"],
                bbox=bbox,
            )

    def _classify_default_layers(self):
        """
        Auto-classify layers into Metal, Via, Pad, or Boundary.
        Default heuristic:
        - Layer >= 60: boundary or passives
        - Layer 10 or contains labels with 'VDD'/'PAD'/'C4': pad
        - Even layers (2, 4, 6): vias between adjacent odd metal layers
        - Odd layers (1, 3, 5, 7): metals
        """
        metal_idx = 0
        sorted_layers = sorted(self.layers.keys())
        
        # Check label layers for pads
        pad_layers = {
            lbl["layer"]
            for lbl in self.labels
            if any(k in lbl["text"].upper() for k in ["VDD", "PAD", "C4", "PWR", "VSS", "GND", "PIN", "IO"])
        }
        
        for layer in sorted_layers:
            info = self.layers[layer]
            if layer in pad_layers or layer == 10:
                info.role = "pad"
                info.name = f"PAD / C4 (L{layer})"
            elif layer >= 60:
                info.role = "boundary"
                info.name = f"Boundary (L{layer})"
            elif layer % 2 == 0 and layer < 10:
                info.role = "via"
                info.name = f"Via {layer // 2} (L{layer})"
                info.via_resistance = 1.5
            else:
                info.role = "metal"
                info.metal_index = metal_idx
                info.name = f"Metal {metal_idx + 1} (L{layer})"
                # Higher metals have lower sheet resistance
                # M1: 0.12, M2: 0.08, M3: 0.05, M4: 0.02 Ohm/sq
                info.sheet_resistance = max(0.015, 0.12 / (1 + 0.6 * metal_idx))
                metal_idx += 1

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
        }
