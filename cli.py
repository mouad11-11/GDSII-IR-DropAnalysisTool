#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

from irdrop import run_analysis


def main():
    parser = argparse.ArgumentParser(
        description="GDSII IR-drop analysis and margin signoff verification tool."
    )
    parser.add_argument("gds_file", type=str, help="Path to input GDSII layout file (.gds / .gds2)")
    parser.add_argument("--vnom", type=float, default=1.0, help="Nominal supply voltage V_nom in Volts (default: 1.0)")
    parser.add_argument("--limit-mv", type=float, default=50.0, help="IR drop tolerance threshold in mV (default: 50.0)")
    parser.add_argument("--limit-pct", type=float, default=None, help="IR drop tolerance as percentage of V_nom")
    parser.add_argument("--current", type=float, default=0.4, help="Total supply current in Amperes (default: 0.4)")
    parser.add_argument(
        "--dist",
        type=str,
        default="uniform",
        choices=["uniform", "center_hotspot", "dual_hotspot", "quad_hotspot"],
        help="Spatial current distribution profile",
    )
    parser.add_argument("--res", type=int, default=100, help="Grid resolution per axis (default: 100)")
    parser.add_argument("--output", type=str, default="report", help="Output directory for reports and plots")
    parser.add_argument("--no-overlay", action="store_true", help="Do not overlay layout wires on heatmaps")
    parser.add_argument("--no-contours", action="store_true", help="Do not draw isopotential contour lines")

    args = parser.parse_args()

    gds_path = Path(args.gds_file)
    if not gds_path.exists():
        print(f"Error: file not found: {gds_path}", file=sys.stderr)
        sys.exit(1)

    limit_mv = args.limit_mv
    if args.limit_pct is not None:
        limit_mv = (args.limit_pct / 100.0) * args.vnom * 1000.0

    print(f"[INFO] Reading layout: {gds_path.name}")
    print(f"[INFO] Config: Vnom={args.vnom:.3f}V, Limit={limit_mv:.1f}mV, Itotal={args.current * 1000:.1f}mA, Dist={args.dist}, Grid={args.res}x{args.res}")

    layout, result, analysis, visualizer = run_analysis(
        str(gds_path),
        v_nom=args.vnom,
        delta_v_limit_mv=limit_mv,
        total_current=args.current,
        distribution=args.dist,
        grid_resolution=(args.res, args.res),
    )

    print(f"[INFO] Solve finished in {result.solve_time_seconds:.3f}s")
    print(f"[INFO] Signoff status: {analysis.status}")
    print(f"  Precise margin:       {analysis.margin_mv:+.2f} mV ({analysis.margin_percentage:+.1f}%)")
    print(f"  Max IR drop:          {analysis.delta_v_max_mv:.2f} mV")
    print(f"  Min observed voltage: {analysis.min_observed_voltage_v:.4f} V (Limit: {analysis.min_allowed_voltage_v:.4f} V)")
    print(f"  Avg IR drop:          {analysis.delta_v_avg_mv:.2f} mV (std={analysis.delta_v_std_mv:.2f} mV)")
    print(f"  Safe current budget:  {analysis.max_safe_current_ma:.1f} mA (Headroom: {analysis.current_headroom_ma:+.1f} mA)")
    print(f"  Effective resistance: {analysis.effective_pdn_resistance_ohm:.4f} Ohm (Peak: {analysis.peak_pdn_resistance_ohm:.4f} Ohm)")
    print(f"  Worst node:           ({analysis.worst_node['x_um']:.1f}, {analysis.worst_node['y_um']:.1f}) um on Layer {analysis.worst_node['layer']}")
    print(f"  Violating area:       {analysis.violating_area_percentage:.2f}% ({analysis.violating_area_um2:.1f} um^2)")
    print(f"  Hotspot clusters:     {len(analysis.hotspots)}")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    heatmap_png = out_dir / f"{gds_path.stem}_ir_drop_heatmap.png"
    fig_heatmap = visualizer.generate_heatmap_figure(
        mode="ir_drop",
        show_layout_overlay=not args.no_overlay,
        show_contours=not args.no_contours,
    )
    fig_heatmap.savefig(heatmap_png, bbox_inches="tight", dpi=160)

    slack_png = out_dir / f"{gds_path.stem}_margin_slack_heatmap.png"
    fig_slack = visualizer.generate_heatmap_figure(mode="margin_slack", show_layout_overlay=not args.no_overlay)
    fig_slack.savefig(slack_png, bbox_inches="tight", dpi=160)

    surf_png = out_dir / f"{gds_path.stem}_3d_surface.png"
    fig_surf = visualizer.generate_3d_surface_figure()
    fig_surf.savefig(surf_png, bbox_inches="tight", dpi=140)

    hist_png = out_dir / f"{gds_path.stem}_histogram.png"
    fig_hist = visualizer.generate_histogram_figure()
    fig_hist.savefig(hist_png, bbox_inches="tight", dpi=140)

    cutline_png = out_dir / f"{gds_path.stem}_cutline_profile.png"
    fig_cut = visualizer.generate_cutline_figure()
    fig_cut.savefig(cutline_png, bbox_inches="tight", dpi=160)

    csv_file = out_dir / f"{gds_path.stem}_violations.csv"
    with open(csv_file, "w") as cf:
        cf.write("Node_Index,X_um,Y_um,IR_Drop_mV,Voltage_V,Margin_Slack_mV\n")
        nodes_to_write = analysis.violating_nodes if analysis.violating_nodes else [
            {"x_um": analysis.worst_node["x_um"], "y_um": analysis.worst_node["y_um"], "drop_mv": analysis.delta_v_max_mv, "voltage_v": analysis.min_observed_voltage_v, "margin_mv": analysis.margin_mv}
        ]
        for idx, n in enumerate(nodes_to_write, start=1):
            cf.write(f"{idx},{n['x_um']},{n['y_um']},{n['drop_mv']},{n['voltage_v']},{n['margin_mv']}\n")

    report_data = {
        "file": str(gds_path),
        "v_nom": args.vnom,
        "delta_v_limit_mv": limit_mv,
        "min_allowed_voltage_v": analysis.min_allowed_voltage_v,
        "delta_v_max_mv": analysis.delta_v_max_mv,
        "min_observed_voltage_v": analysis.min_observed_voltage_v,
        "precise_margin_mv": analysis.margin_mv,
        "margin_percentage": analysis.margin_percentage,
        "status": analysis.status,
        "is_safe": analysis.is_safe,
        "max_safe_current_ma": analysis.max_safe_current_ma,
        "current_headroom_ma": analysis.current_headroom_ma,
        "max_safe_power_w": analysis.max_safe_power_w,
        "effective_pdn_resistance_ohm": analysis.effective_pdn_resistance_ohm,
        "worst_node": analysis.worst_node,
        "violating_area_percentage": analysis.violating_area_percentage,
        "violating_area_um2": analysis.violating_area_um2,
        "hotspots": [
            {
                "id": h.id,
                "bbox_um": h.bbox_um,
                "center_um": h.center_um,
                "area_um2": h.area_um2,
                "max_drop_mv": h.max_drop_mv,
                "violation_severity_mv": h.violation_severity_mv,
            }
            for h in analysis.hotspots
        ],
        "layer_metrics": analysis.layer_metrics,
    }
    report_json = out_dir / f"{gds_path.stem}_signoff_report.json"
    with open(report_json, "w") as f:
        json.dump(report_data, f, indent=2)

    print(f"[INFO] Wrote report and figures to: {out_dir}")


if __name__ == "__main__":
    main()
