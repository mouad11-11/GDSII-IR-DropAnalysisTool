"""IR-drop signoff analyzer and margin calculator."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import cv2
import numpy as np

from irdrop.solver import SolverResult


@dataclass
class HotspotRegion:
    id: int
    bbox_um: Tuple[float, float, float, float]  # (min_x, min_y, max_x, max_y)
    center_um: Tuple[float, float]
    area_um2: float
    max_drop_mv: float
    avg_drop_mv: float
    violation_severity_mv: float  # max_drop_mv - limit_mv


@dataclass
class MarginAnalysisResult:
    # Supply and Threshold
    v_nom: float
    delta_v_limit_mv: float
    min_allowed_voltage_v: float
    
    # Observed Values
    delta_v_max_mv: float
    min_observed_voltage_v: float
    delta_v_avg_mv: float
    delta_v_std_mv: float
    p90_drop_mv: float
    p95_drop_mv: float
    p99_drop_mv: float

    # Precise Value Margin Metrics
    margin_mv: float           # delta_v_limit_mv - delta_v_max_mv (positive is PASS, negative is VIOLATION)
    margin_percentage: float   # (margin_mv / delta_v_limit_mv) * 100
    status: str                # 'PASS' | 'VIOLATION'
    is_safe: bool              # True if margin_mv >= 0

    # Worst-case Hotspot Pinpoint
    worst_node: Dict[str, Any]  # {'x_um', 'y_um', 'drop_mv', 'voltage_v', 'layer'}

    # Violation Region Metrics
    violating_node_count: int
    total_active_nodes: int
    violating_area_um2: float
    total_active_area_um2: float
    violating_area_percentage: float
    hotspots: List[HotspotRegion]

    # Layer-by-layer breakdown
    layer_metrics: List[Dict[str, Any]]

    # Histogram for distribution plots
    histogram_bins_mv: List[float]
    histogram_counts: List[int]
    # 2D Margin Slack Grid (delta_v_limit_mv - delta_v_mv)
    margin_slack_grid_mv: np.ndarray

    # Power & Current Budgeting Signoff
    max_safe_current_ma: float = 0.0
    current_headroom_ma: float = 0.0
    max_safe_power_w: float = 0.0
    current_power_w: float = 0.0
    effective_pdn_resistance_ohm: float = 0.0
    peak_pdn_resistance_ohm: float = 0.0

    # Detailed node violation list (for CSV export)
    violating_nodes: List[Dict[str, Any]] = field(default_factory=list)


class IRDropAnalyzer:
    def __init__(self, solver_result: SolverResult, delta_v_limit_mv: float = 50.0):
        self.result = solver_result
        self.limit_mv = float(delta_v_limit_mv)

    def analyze(self) -> MarginAnalysisResult:
        res = self.result
        v_nom = res.v_nom
        limit_mv = self.limit_mv
        min_allowed_v = v_nom - (limit_mv / 1000.0)

        # Active nodes evaluation
        # Gather all active points from composite IR drop
        act_mask = res.active_die_mask
        if not np.any(act_mask):
            act_mask = np.ones_like(res.composite_ir_drop_v, dtype=bool)

        active_drops_v = res.composite_ir_drop_v[act_mask]
        active_drops_mv = active_drops_v * 1000.0

        delta_v_max_mv = float(np.max(active_drops_mv)) if len(active_drops_mv) > 0 else 0.0
        delta_v_avg_mv = float(np.mean(active_drops_mv)) if len(active_drops_mv) > 0 else 0.0
        delta_v_std_mv = float(np.std(active_drops_mv)) if len(active_drops_mv) > 0 else 0.0
        
        p90 = float(np.percentile(active_drops_mv, 90)) if len(active_drops_mv) > 0 else 0.0
        p95 = float(np.percentile(active_drops_mv, 95)) if len(active_drops_mv) > 0 else 0.0
        p99 = float(np.percentile(active_drops_mv, 99)) if len(active_drops_mv) > 0 else 0.0

        min_observed_v = v_nom - (delta_v_max_mv / 1000.0)

        # PRECISE VALUE MARGIN
        margin_mv = round(limit_mv - delta_v_max_mv, 3)
        margin_pct = round((margin_mv / max(limit_mv, 1e-6)) * 100.0, 2)
        is_safe = margin_mv >= 0.0
        status = "PASS" if is_safe else "VIOLATION"

        # Worst node coordinates
        worst_r, worst_c = np.unravel_index(np.argmax(res.composite_ir_drop_v), res.composite_ir_drop_v.shape)
        worst_x = float(res.x_coords_um[worst_c])
        worst_y = float(res.y_coords_um[worst_r])

        # Identify worst layer at this coordinate
        worst_layer = -1
        max_layer_drop = -1.0
        for l, d_grid in res.layer_ir_drops.items():
            if d_grid[worst_r, worst_c] > max_layer_drop:
                max_layer_drop = d_grid[worst_r, worst_c]
                worst_layer = l

        worst_node = {
            "x_um": round(worst_x, 2),
            "y_um": round(worst_y, 2),
            "grid_r": int(worst_r),
            "grid_c": int(worst_c),
            "drop_mv": round(delta_v_max_mv, 2),
            "voltage_v": round(min_observed_v, 4),
            "layer": worst_layer,
        }

        # Violations and Hotspot Clustering
        # Create binary violation mask
        violation_mask = (res.smoothed_ir_drop_mv > limit_mv).astype(np.uint8)
        violating_nodes = int(np.sum(active_drops_mv > limit_mv))
        total_active_nodes = int(len(active_drops_mv))

        # Area calculation
        dx = float(res.x_coords_um[1] - res.x_coords_um[0]) if len(res.x_coords_um) > 1 else 1.0
        dy = float(res.y_coords_um[1] - res.y_coords_um[0]) if len(res.y_coords_um) > 1 else 1.0
        cell_area = dx * dy

        total_active_area = total_active_nodes * cell_area
        violating_area = violating_nodes * cell_area
        violating_area_pct = round((violating_area / max(total_active_area, 1e-6)) * 100.0, 2)

        # Connected component analysis for hotspots
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(violation_mask, connectivity=8)
        hotspots: List[HotspotRegion] = []

        for h_id in range(1, num_labels):
            h_mask = labels == h_id
            h_pixels = int(stats[h_id, cv2.CC_STAT_AREA])
            if h_pixels < 2:
                continue

            h_area_um2 = h_pixels * cell_area
            left = stats[h_id, cv2.CC_STAT_LEFT]
            top = stats[h_id, cv2.CC_STAT_TOP]
            w = stats[h_id, cv2.CC_STAT_WIDTH]
            h = stats[h_id, cv2.CC_STAT_HEIGHT]

            # Bounding box in um
            min_x_h = float(res.x_coords_um[left])
            max_x_h = float(res.x_coords_um[min(left + w, len(res.x_coords_um) - 1)])
            min_y_h = float(res.y_coords_um[top])
            max_y_h = float(res.y_coords_um[min(top + h, len(res.y_coords_um) - 1)])

            cx = float(res.x_coords_um[int(round(centroids[h_id][0]))])
            cy = float(res.y_coords_um[int(round(centroids[h_id][1]))])

            h_drops = res.smoothed_ir_drop_mv[h_mask]
            h_max_drop = float(np.max(h_drops))
            h_avg_drop = float(np.mean(h_drops))

            hotspots.append(
                HotspotRegion(
                    id=h_id,
                    bbox_um=(round(min_x_h, 2), round(min_y_h, 2), round(max_x_h, 2), round(max_y_h, 2)),
                    center_um=(round(cx, 2), round(cy, 2)),
                    area_um2=round(h_area_um2, 2),
                    max_drop_mv=round(h_max_drop, 2),
                    avg_drop_mv=round(h_avg_drop, 2),
                    violation_severity_mv=round(h_max_drop - limit_mv, 2),
                )
            )

        # Sort hotspots by severity
        hotspots.sort(key=lambda h: h.violation_severity_mv, reverse=True)

        # Layer-by-layer breakdown
        layer_metrics = []
        for l in sorted(res.layer_ir_drops.keys()):
            l_mask = res.layer_active_mask[l]
            if np.any(l_mask):
                l_drops_mv = res.layer_ir_drops[l][l_mask] * 1000.0
                l_max = float(np.max(l_drops_mv))
                l_avg = float(np.mean(l_drops_mv))
                l_margin = round(limit_mv - l_max, 2)
            else:
                l_max, l_avg, l_margin = 0.0, 0.0, round(limit_mv, 2)

            layer_metrics.append({
                "layer": l,
                "max_drop_mv": round(l_max, 2),
                "avg_drop_mv": round(l_avg, 2),
                "margin_mv": l_margin,
                "status": "PASS" if l_margin >= 0 else "VIOLATION",
            })

        # Histogram calculation
        counts, bin_edges = np.histogram(active_drops_mv, bins=25)
        hist_bins = [round(float(b), 2) for b in bin_edges]
        hist_counts = [int(c) for c in counts]

        # Margin slack grid: positive = safe slack, negative = violation
        margin_slack_grid = limit_mv - res.smoothed_ir_drop_mv

        # Power & Current Budgeting
        total_curr_a = res.total_current
        if delta_v_max_mv > 0.0:
            max_safe_current_ma = round((total_curr_a * (limit_mv / delta_v_max_mv)) * 1000.0, 2)
        else:
            max_safe_current_ma = round(total_curr_a * 1000.0 * 2.0, 2)

        current_headroom_ma = round(max_safe_current_ma - (total_curr_a * 1000.0), 2)
        current_power_w = round(v_nom * total_curr_a, 4)
        max_safe_power_w = round(v_nom * (max_safe_current_ma / 1000.0), 4)

        effective_pdn_res = round((delta_v_avg_mv / 1000.0) / max(total_curr_a, 1e-6), 4)
        peak_pdn_res = round((delta_v_max_mv / 1000.0) / max(total_curr_a, 1e-6), 4)

        # Collect top violating nodes for CSV export / detailed inspection
        violating_nodes_list = []
        if violating_nodes > 0:
            viol_indices = np.where(res.smoothed_ir_drop_mv > limit_mv)
            for r, c in zip(viol_indices[0][:500], viol_indices[1][:500]):
                drop_val = float(res.smoothed_ir_drop_mv[r, c])
                violating_nodes_list.append({
                    "x_um": round(float(res.x_coords_um[c]), 2),
                    "y_um": round(float(res.y_coords_um[r]), 2),
                    "drop_mv": round(drop_val, 2),
                    "voltage_v": round(v_nom - drop_val / 1000.0, 4),
                    "margin_mv": round(limit_mv - drop_val, 2),
                })

        return MarginAnalysisResult(
            v_nom=v_nom,
            delta_v_limit_mv=limit_mv,
            min_allowed_voltage_v=round(min_allowed_v, 4),
            delta_v_max_mv=round(delta_v_max_mv, 2),
            min_observed_voltage_v=round(min_observed_v, 4),
            delta_v_avg_mv=round(delta_v_avg_mv, 2),
            delta_v_std_mv=round(delta_v_std_mv, 2),
            p90_drop_mv=round(p90, 2),
            p95_drop_mv=round(p95, 2),
            p99_drop_mv=round(p99, 2),
            margin_mv=margin_mv,
            margin_percentage=margin_pct,
            status=status,
            is_safe=is_safe,
            worst_node=worst_node,
            violating_node_count=violating_nodes,
            total_active_nodes=total_active_nodes,
            violating_area_um2=round(violating_area, 2),
            total_active_area_um2=round(total_active_area, 2),
            violating_area_percentage=violating_area_pct,
            hotspots=hotspots,
            layer_metrics=layer_metrics,
            histogram_bins_mv=hist_bins,
            histogram_counts=hist_counts,
            margin_slack_grid_mv=margin_slack_grid,
            max_safe_current_ma=max_safe_current_ma,
            current_headroom_ma=current_headroom_ma,
            max_safe_power_w=max_safe_power_w,
            current_power_w=current_power_w,
            effective_pdn_resistance_ohm=effective_pdn_res,
            peak_pdn_resistance_ohm=peak_pdn_res,
            violating_nodes=violating_nodes_list,
        )
