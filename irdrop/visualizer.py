"""Layout IR-drop heatmap and profile visualization."""

import base64
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

DARK_BG = "#03060d"
DARK_CARD = "#080d1a"
TEXT_COLOR = "#f1f5f9"
MUTED_TEXT = "#94a3b8"
GRID_COLOR = "#151e2e"
SPINE_COLOR = "#243247"

plt.style.use("dark_background")
matplotlib.rcParams.update({
    "font.family": "monospace",
    "figure.facecolor": DARK_BG,
    "axes.facecolor": DARK_BG,
    "savefig.facecolor": DARK_BG,
    "text.color": TEXT_COLOR,
    "axes.labelcolor": TEXT_COLOR,
    "xtick.color": MUTED_TEXT,
    "ytick.color": MUTED_TEXT,
    "axes.edgecolor": SPINE_COLOR,
    "grid.color": GRID_COLOR,
})

from irdrop.analyzer import MarginAnalysisResult
from irdrop.gds_parser import GDSLayout
from irdrop.solver import SolverResult


class IRDropVisualizer:
    def __init__(self, layout: GDSLayout, solver_result: SolverResult, analysis: MarginAnalysisResult):
        self.layout = layout
        self.result = solver_result
        self.analysis = analysis

    def generate_heatmap_figure(
        self,
        mode: str = "ir_drop",  # 'ir_drop', 'voltage', 'margin_slack'
        show_layout_overlay: bool = True,
        show_contours: bool = True,
        show_pads: bool = True,
        show_worst_marker: bool = True,
        cmap_name: str = "turbo",
        dpi: int = 150,
    ) -> plt.Figure:
        """Generate 2D potential or voltage drop distribution figure."""
        res = self.result
        analysis = self.analysis
        min_x, min_y, max_x, max_y = self.layout.bbox

        fig, ax = plt.subplots(figsize=(10, 8), dpi=dpi)

        # Determine data and labels based on mode
        if mode == "voltage":
            # Absolute voltage V(x, y)
            data = res.v_nom - (res.smoothed_ir_drop_mv / 1000.0)
            cbar_label = "Supply Voltage V(x,y) [V]"
            title_text = f"PDN Potential Landscape (Nominal: {res.v_nom:.2f} V)"
            cmap = "turbo_r" if cmap_name == "turbo" else cmap_name
            vmin = max(0.0, res.v_nom - (analysis.delta_v_limit_mv * 1.5 / 1000.0))
            vmax = res.v_nom
        elif mode == "margin_slack":
            # Margin slack = Delta_V_limit - Delta_V(x, y)
            data = analysis.margin_slack_grid_mv
            cbar_label = "Local Margin Slack (Limit - Drop) [mV]"
            title_text = f"Precise Margin Map (Threshold Limit: {analysis.delta_v_limit_mv:.1f} mV)"
            cmap = "RdYlGn"
            v_abs = max(abs(np.min(data)), abs(np.max(data)), 10.0)
            vmin, vmax = -v_abs, v_abs
        else:
            # Default: IR Drop in mV
            data = res.smoothed_ir_drop_mv
            cbar_label = "IR-Drop ΔV(x,y) [mV]"
            title_text = f"Static IR-Drop Heatmap (Total Current: {res.total_current*1000:.0f} mA)"
            cmap = cmap_name
            vmin = 0.0
            vmax = max(analysis.delta_v_limit_mv * 1.25, analysis.delta_v_max_mv * 1.05)

        # Plot 2D Heatmap (extent: left, right, bottom, top)
        # Note: image origin='lower' so (0,0) is at bottom-left
        extent = [min_x, max_x, min_y, max_y]
        im = ax.imshow(
            data,
            origin="lower",
            extent=extent,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            aspect="equal",
            interpolation="bicubic",
        )

        # Colorbar
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label(cbar_label, fontsize=11, fontweight="bold", labelpad=10)

        # Add limit contour line if plotting IR-drop
        if mode == "ir_drop" and show_contours:
            # Draw contour at margin limit threshold
            X, Y = np.meshgrid(res.x_coords_um, res.y_coords_um)
            if np.any(data >= analysis.delta_v_limit_mv):
                cs_viol = ax.contour(
                    X, Y, data,
                    levels=[analysis.delta_v_limit_mv],
                    colors=["#ff0033"],
                    linewidths=2.2,
                    linestyles="--",
                )
                ax.clabel(cs_viol, inline=True, fmt=f"Limit: {analysis.delta_v_limit_mv:.0f}mV", fontsize=9)
            
            # Subtle intermediate isopotential contours
            cs = ax.contour(X, Y, data, levels=6, colors=[(1.0, 1.0, 1.0, 0.35)], linewidths=0.7)
            ax.clabel(cs, inline=True, fontsize=8, fmt="%.0f mV")

        # Overlay physical GDSII layout metal wires
        if show_layout_overlay:
            from matplotlib.collections import PolyCollection
            metal_colors = {
                1: "#00e5ff",
                3: "#76ff03",
                5: "#ffd600",
                7: "#ff6d00",
                8: "#a78bfa",
                14: "#f472b6",
                19: "#38bdf8",
                30: "#4ade80",
            }
            for layer, polys in self.layout.polygons_by_layer.items():
                if polys:
                    color = metal_colors.get(layer, "#64748b")
                    info = self.layout.layers.get(layer)
                    if layer in metal_colors or (info and info.role == "metal"):
                        step = max(1, len(polys) // 4000)
                        sample_polys = polys[::step]
                        pc = PolyCollection(sample_polys, closed=True, facecolors="none", edgecolors=color, linewidths=0.5, alpha=0.25)
                        ax.add_collection(pc)

        # Overlay Power Pads / C4 Bumps
        if show_pads:
            from matplotlib.collections import PolyCollection
            pad_layers = [l for l, info in self.layout.layers.items() if info.role == "pad"]
            for pl in pad_layers:
                polys = self.layout.polygons_by_layer.get(pl, [])
                if polys:
                    pc = PolyCollection(polys, closed=True, facecolors="#ffffff", edgecolors="#000000", linewidths=1.0, alpha=0.6)
                    ax.add_collection(pc)

            # Label pads
            for lbl in self.layout.labels:
                if any(k in lbl["text"].upper() for k in ["VDD", "PAD", "C4"]):
                    ax.plot(lbl["x"], lbl["y"], marker="o", markersize=6, color="#00ffcc", markeredgecolor="black")

        # Mark Worst-Case IR Drop Node
        if show_worst_marker:
            wn = analysis.worst_node
            wx, wy = wn["x_um"], wn["y_um"]
            ax.plot(wx, wy, marker="X", markersize=12, color="#ff0055", markeredgecolor="white", markeredgewidth=1.5, zorder=10)
            
            # Annotation callout box
            status_color = "#10b981" if analysis.is_safe else "#ff334b"
            callout_text = (
                f"Worst Hotspot\n"
                f"({wx:.1f}, {wy:.1f}) µm\n"
                f"ΔV: {wn['drop_mv']:.2f} mV\n"
                f"Margin: {analysis.margin_mv:+.2f} mV"
            )
            ax.annotate(
                callout_text,
                xy=(wx, wy),
                xytext=(wx + (max_x - min_x) * 0.08, wy + (max_y - min_y) * 0.08),
                bbox=dict(boxstyle="round,pad=0.5", fc=DARK_CARD, ec=status_color, lw=1.6, alpha=0.95),
                arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=.2", color="#ffffff", lw=1.5),
                fontsize=9,
                fontweight="bold",
                color=TEXT_COLOR,
                zorder=11,
            )

        # Title & Margin Status Header Banner
        status_str = f"STATUS: {analysis.status}"
        margin_str = f"Precise Margin: {analysis.margin_mv:+.2f} mV ({analysis.margin_percentage:+.1f}%)"
        
        ax.set_title(
            f"{title_text}\n{status_str} | {margin_str} | Max Drop: {analysis.delta_v_max_mv:.2f} mV",
            fontsize=11,
            fontweight="bold",
            color=TEXT_COLOR,
            pad=14,
        )

        ax.set_xlabel("X Coordinate (µm)", fontsize=10, fontweight="bold", color=TEXT_COLOR)
        ax.set_ylabel("Y Coordinate (µm)", fontsize=10, fontweight="bold", color=TEXT_COLOR)
        ax.set_xlim(min_x, max_x)
        ax.set_ylim(min_y, max_y)
        ax.grid(True, linestyle=":", alpha=0.3, color=GRID_COLOR)
        fig.patch.set_facecolor(DARK_BG)
        ax.set_facecolor(DARK_BG)

        plt.tight_layout()
        return fig

    def generate_cutline_figure(self, cutline_y_um: Optional[float] = None) -> plt.Figure:
        """1D cross-section voltage drop profile."""
        res = self.result
        analysis = self.analysis
        
        # Pick cutline at worst hotspot Y coordinate if not specified
        if cutline_y_um is None:
            cutline_y_um = analysis.worst_node["y_um"]

        # Find closest row index
        r_idx = int(np.argmin(np.abs(res.y_coords_um - cutline_y_um)))
        actual_y = res.y_coords_um[r_idx]

        x_vals = res.x_coords_um
        drop_profile_mv = res.smoothed_ir_drop_mv[r_idx, :]

        fig, ax = plt.subplots(figsize=(8, 4), dpi=130)
        fig.patch.set_facecolor(DARK_BG)
        ax.set_facecolor(DARK_BG)

        ax.plot(x_vals, drop_profile_mv, color="#00f2fe", lw=2.5, label=f"IR-Drop Profile (Y = {actual_y:.1f} µm)")
        ax.axhline(analysis.delta_v_limit_mv, color="#ff334b", lw=1.8, linestyle="--", label=f"Limit ({analysis.delta_v_limit_mv:.1f} mV)")

        # Fill violation region
        ax.fill_between(
            x_vals,
            drop_profile_mv,
            analysis.delta_v_limit_mv,
            where=(drop_profile_mv > analysis.delta_v_limit_mv),
            color="#ff334b",
            alpha=0.3,
            label="Violation Zone",
        )

        ax.set_title(f"1D Voltage Drop Cutline at Y = {actual_y:.1f} µm", fontsize=11, fontweight="bold", color=TEXT_COLOR)
        ax.set_xlabel("X Coordinate (µm)", fontsize=10, color=TEXT_COLOR)
        ax.set_ylabel("IR-Drop (mV)", fontsize=10, color=TEXT_COLOR)
        ax.legend(loc="upper right", fontsize=8.5, facecolor=DARK_CARD, edgecolor=SPINE_COLOR, labelcolor=TEXT_COLOR)
        ax.grid(True, linestyle=":", alpha=0.4, color=GRID_COLOR)

        plt.tight_layout()
        return fig

    def generate_3d_surface_figure(self, cmap_name: str = "turbo", dpi: int = 140) -> plt.Figure:
        """3D isometric surface plot of voltage drop."""
        res = self.result
        analysis = self.analysis

        fig = plt.figure(figsize=(9, 6.5), dpi=dpi)
        fig.patch.set_facecolor(DARK_BG)
        ax = fig.add_subplot(111, projection="3d")
        ax.set_facecolor(DARK_BG)

        ny, nx = res.smoothed_ir_drop_mv.shape
        stride_x = max(1, nx // 60)
        stride_y = max(1, ny // 60)

        X, Y = np.meshgrid(res.x_coords_um[::stride_x], res.y_coords_um[::stride_y])
        Z = res.smoothed_ir_drop_mv[::stride_y, ::stride_x]

        surf = ax.plot_surface(
            X, Y, Z,
            cmap=cmap_name,
            edgecolor="none",
            antialiased=True,
            rstride=1,
            cstride=1,
            alpha=0.92,
        )

        # Style 3D panes dark
        ax.xaxis.set_pane_color((0.02, 0.03, 0.07, 1.0))
        ax.yaxis.set_pane_color((0.02, 0.03, 0.07, 1.0))
        ax.zaxis.set_pane_color((0.02, 0.03, 0.07, 1.0))

        cbar = fig.colorbar(surf, ax=ax, shrink=0.55, aspect=10, pad=0.1)
        cbar.set_label("IR-Drop ΔV [mV]", fontsize=10, fontweight="bold", color=TEXT_COLOR)
        cbar.ax.yaxis.set_tick_params(color=MUTED_TEXT)
        cbar.outline.set_edgecolor(SPINE_COLOR)

        ax.set_title(
            f"3D Voltage Drop Landscape (Max Drop: {analysis.delta_v_max_mv:.2f} mV, Margin: {analysis.margin_mv:+.2f} mV)",
            fontsize=11,
            fontweight="bold",
            color=TEXT_COLOR,
            pad=14,
        )
        ax.set_xlabel("X (µm)", fontsize=9, labelpad=8, color=MUTED_TEXT)
        ax.set_ylabel("Y (µm)", fontsize=9, labelpad=8, color=MUTED_TEXT)
        ax.set_zlabel("ΔV (mV)", fontsize=9, labelpad=8, color=MUTED_TEXT)
        ax.view_init(elev=32, azim=-55)

        plt.tight_layout()
        return fig

    def generate_histogram_figure(self, dpi: int = 130) -> plt.Figure:
        """Histogram of node IR-drop distribution."""
        res = self.result
        analysis = self.analysis

        act_mask = res.active_die_mask
        drops = res.composite_ir_drop_v[act_mask] * 1000.0 if np.any(act_mask) else res.smoothed_ir_drop_mv.flatten()

        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=dpi)
        fig.patch.set_facecolor(DARK_BG)
        ax.set_facecolor(DARK_BG)

        n, bins, patch_list = ax.hist(drops, bins=35, color="#0284c7", edgecolor="#0369a1", alpha=0.75, density=False)

        for i in range(len(patch_list)):
            if bins[i] > analysis.delta_v_limit_mv:
                patch_list[i].set_facecolor("#ff334b")
                patch_list[i].set_edgecolor("#dc2626")

        ax.axvline(analysis.delta_v_limit_mv, color="#ff334b", lw=2.2, linestyle="--", label=f"Tolerance Limit ({analysis.delta_v_limit_mv:.1f} mV)")
        ax.axvline(analysis.delta_v_avg_mv, color="#10b981", lw=1.8, linestyle="-.", label=f"Mean Drop ({analysis.delta_v_avg_mv:.1f} mV)")
        ax.axvline(analysis.p95_drop_mv, color="#f59e0b", lw=1.8, linestyle=":", label=f"P95 Drop ({analysis.p95_drop_mv:.1f} mV)")

        status_text = f"PASS (+{analysis.margin_mv:.1f} mV slack)" if analysis.is_safe else f"VIOLATION ({analysis.margin_mv:.1f} mV over)"
        ax.set_title(f"IR-Drop Distribution & Margin Slack Verification\nStatus: {status_text}", fontsize=11, fontweight="bold", color=TEXT_COLOR)
        ax.set_xlabel("IR-Drop (mV)", fontsize=10, color=TEXT_COLOR)
        ax.set_ylabel("Node Frequency", fontsize=10, color=TEXT_COLOR)
        ax.legend(loc="upper right", fontsize=8.5, facecolor=DARK_CARD, edgecolor=SPINE_COLOR, labelcolor=TEXT_COLOR)
        ax.grid(True, linestyle=":", alpha=0.4, color=GRID_COLOR)

        plt.tight_layout()
        return fig

    def generate_current_flow_figure(self, dpi: int = 140) -> plt.Figure:
        """2D current flow vector map."""
        res = self.result
        analysis = self.analysis
        min_x, min_y, max_x, max_y = self.layout.bbox

        V = res.v_nom - (res.smoothed_ir_drop_mv / 1000.0)
        dydx = np.gradient(V)
        Jx = -dydx[1]
        Jy = -dydx[0]

        fig, ax = plt.subplots(figsize=(9, 7.5), dpi=dpi)
        fig.patch.set_facecolor(DARK_BG)
        ax.set_facecolor(DARK_BG)
        extent = [min_x, max_x, min_y, max_y]

        im = ax.imshow(
            res.smoothed_ir_drop_mv,
            origin="lower",
            extent=extent,
            cmap="turbo",
            alpha=0.82,
            aspect="equal",
        )
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label("IR-Drop ΔV [mV]", fontsize=10, fontweight="bold", color=TEXT_COLOR)
        cbar.ax.yaxis.set_tick_params(color=MUTED_TEXT)
        cbar.outline.set_edgecolor(SPINE_COLOR)

        ny, nx = V.shape
        step_x = max(1, nx // 22)
        step_y = max(1, ny // 22)

        X, Y = np.meshgrid(res.x_coords_um, res.y_coords_um)
        X_sub = X[::step_y, ::step_x]
        Y_sub = Y[::step_y, ::step_x]
        Jx_sub = Jx[::step_y, ::step_x]
        Jy_sub = Jy[::step_y, ::step_x]

        mag = np.hypot(Jx_sub, Jy_sub)
        mag_mask = mag > 1e-9
        Jx_norm = np.zeros_like(Jx_sub)
        Jy_norm = np.zeros_like(Jy_sub)
        Jx_norm[mag_mask] = Jx_sub[mag_mask] / mag[mag_mask]
        Jy_norm[mag_mask] = Jy_sub[mag_mask] / mag[mag_mask]

        ax.quiver(
            X_sub, Y_sub, Jx_norm, Jy_norm,
            color="#ffffff",
            scale=28,
            width=0.0035,
            headwidth=3.5,
            headlength=4,
            alpha=0.85,
        )

        wn = analysis.worst_node
        ax.plot(wn["x_um"], wn["y_um"], marker="X", markersize=12, color="#ff0055", markeredgecolor="white")

        ax.set_title("PDN Current Flow Vector Field (Arrows: Current Direction -∇V)", fontsize=11, fontweight="bold", color=TEXT_COLOR)
        ax.set_xlabel("X (µm)", fontsize=10, color=TEXT_COLOR)
        ax.set_ylabel("Y (µm)", fontsize=10, color=TEXT_COLOR)
        ax.set_xlim(min_x, max_x)
        ax.set_ylim(min_y, max_y)

        plt.tight_layout()
        return fig

    def to_base64_png(self, fig: plt.Figure) -> str:
        """Converts Matplotlib figure to base64 PNG data URI string."""
        buf = BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", dpi=140, facecolor=fig.get_facecolor(), edgecolor="none")
        plt.close(fig)
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"
