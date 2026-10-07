"""FastAPI backend for GDSII IR-drop analysis."""

from dataclasses import asdict
import html
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Dict, List, Literal, Optional
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from irdrop import GDSLayout, IRDropAnalyzer, IRDropSolver, IRDropVisualizer, PDNBuilder
from irdrop.sample_generator import create_bottleneck_pdn, create_hierarchical_pdn, create_mesh_pdn

app = FastAPI(title="GDSII IR-Drop & Margin Analyzer", version="1.0.0")

# Security configuration
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB upload limit
SESSION_TTL_SECONDS = 3600  # 1 hour session retention

# Enable CORS with explicit trusted origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Directories
BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "samples"
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "web" / "static"

SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory session store: session_id -> dict of cached objects
SESSION_STORE: Dict[str, Dict[str, Any]] = {}


def cleanup_expired_sessions():
    """Evicts expired sessions and removes uploaded layout files."""
    now = time.time()
    expired_ids = [
        sid for sid, data in SESSION_STORE.items()
        if now - data.get("created_at", 0) > SESSION_TTL_SECONDS
    ]
    for sid in expired_ids:
        data = SESSION_STORE.pop(sid, None)
        if data and "gds_path" in data:
            fp = Path(data["gds_path"])
            if fp.exists() and UPLOAD_DIR in fp.parents:
                try:
                    fp.unlink()
                except OSError:
                    pass


def ensure_samples():
    """Ensure sample GDS files exist."""
    mesh_path = SAMPLES_DIR / "mesh_pdn.gds"
    if not mesh_path.exists():
        create_mesh_pdn(str(mesh_path))
    hier_path = SAMPLES_DIR / "hierarchical_pdn.gds"
    if not hier_path.exists():
        create_hierarchical_pdn(str(hier_path))
    bottle_path = SAMPLES_DIR / "bottleneck_pdn.gds"
    if not bottle_path.exists():
        create_bottleneck_pdn(str(bottle_path))


ensure_samples()


class AnalysisRequest(BaseModel):
    file_id: str
    v_nom: float = Field(default=1.0, gt=0.0, le=100.0)
    limit_mv: float = Field(default=50.0, gt=0.0, le=10000.0)
    total_current: float = Field(default=0.4, gt=0.0, le=1000.0)
    distribution: Literal["uniform", "center_hotspot", "dual_hotspot", "quad_hotspot"] = "uniform"
    grid_resolution: int = Field(default=100, ge=10, le=500)
    tech: str = "default"
    guess_layers: bool = False
    target_net: str = "VDD"
    net_layers: Optional[List[int]] = None
    layer_overrides: Optional[Dict[str, Dict[str, Any]]] = None
    heatmap_mode: Literal["ir_drop", "voltage", "margin_slack", "3d_surface", "histogram", "current_flow"] = "ir_drop"
    show_layout_overlay: bool = True
    show_contours: bool = True
    show_pads: bool = True
    show_worst_marker: bool = True
    cmap_name: str = "turbo"
    allow_default_pads: bool = False
    solver_method: Literal["auto", "cg", "amg", "direct"] = "auto"
    max_violation_rows: int = Field(default=1000, ge=0, le=100000)


class CutlineRequest(BaseModel):
    file_id: str
    cutline_y_um: Optional[float] = None


@app.get("/api/techs")
def list_techs():
    """Returns available PDK technology configurations."""
    return [
        {
            "id": "default",
            "name": "Generic Default (Synthetic)",
            "description": "Default multi-layer CMOS PDN configuration for synthetic benchmarks",
        },
        {
            "id": "ihp_sg13g2",
            "name": "IHP SG13G2 (130nm BiCMOS)",
            "description": "Open-source PDK for IHP SG13G2 (M1-M4, TopMetal1, TopMetal2)",
        },
    ]


@app.get("/api/samples")
def list_samples():
    """Returns list of built-in sample GDS files."""
    return [
        {
            "id": "mesh_pdn",
            "name": "Standard Power Mesh PDN",
            "description": "2-layer power mesh (M1 rails + M2 stripes + peripheral VDD pads + center hotspot)",
            "filename": "mesh_pdn.gds",
        },
        {
            "id": "hierarchical_pdn",
            "name": "Hierarchical SoC 4-Metal PDN",
            "description": "4-metal tier PDN (M1 stdcell, M2/M3 distribution, M4 global mesh, 4x4 C4 flip-chip array)",
            "filename": "hierarchical_pdn.gds",
        },
        {
            "id": "bottleneck_pdn",
            "name": "Defective Bottleneck PDN (Violation Demo)",
            "description": "PDN with missing Northeast vias and pinched M2 stripes, creating high IR drop and negative margin",
            "filename": "bottleneck_pdn.gds",
        },
        {
            "id": "ihp_counter",
            "name": "IHP SG13G2 Digital Counter",
            "description": "Real taped-out digital macro (counter_top.gds) from iic-jku/ihp-sg13g2-ams-chip-template",
            "filename": "counter_top.gds",
        },
        {
            "id": "ihp_inverter",
            "name": "IHP SG13G2 Inverter Macro",
            "description": "Real analog/mixed-signal macro (inverter_top.gds) from iic-jku/ihp-sg13g2-ams-chip-template",
            "filename": "inverter_top.gds",
        },
        {
            "id": "ihp_bondpad",
            "name": "IHP SG13G2 70x70 Bond Pad",
            "description": "Physical I/O bond pad IP (sg13g2_ip__bondpad_70x70.gds)",
            "filename": "sg13g2_ip__bondpad_70x70.gds",
        },
    ]


@app.post("/api/load-sample/{sample_id}")
def load_sample(sample_id: str):
    """Loads a built-in benchmark sample."""
    cleanup_expired_sessions()
    sample_files = {
        "mesh_pdn": SAMPLES_DIR / "mesh_pdn.gds",
        "hierarchical_pdn": SAMPLES_DIR / "hierarchical_pdn.gds",
        "bottleneck_pdn": SAMPLES_DIR / "bottleneck_pdn.gds",
        "ihp_counter": SAMPLES_DIR / "ihp" / "counter_top.gds",
        "ihp_inverter": SAMPLES_DIR / "ihp" / "inverter_top.gds",
        "ihp_bondpad": SAMPLES_DIR / "ihp" / "sg13g2_ip__bondpad_70x70.gds",
    }
    if sample_id not in sample_files:
        raise HTTPException(status_code=404, detail="Sample not found")

    src_file = sample_files[sample_id]
    file_id = str(uuid.uuid4())
    dst_file = UPLOAD_DIR / f"{file_id}.gds"
    shutil.copyfile(src_file, dst_file)

    default_tech = "ihp_sg13g2" if sample_id.startswith("ihp_") else "default"
    layout = GDSLayout(str(dst_file), tech=default_tech)
    SESSION_STORE[file_id] = {
        "gds_path": str(dst_file),
        "layout": layout,
        "filename": src_file.name,
        "tech": default_tech,
        "guess_layers": False,
        "created_at": time.time(),
    }

    return {
        "file_id": file_id,
        "filename": src_file.name,
        "tech": default_tech,
        "summary": layout.get_summary(),
    }


@app.post("/api/upload")
async def upload_gds(file: UploadFile = File(...)):
    """Uploads and parses a user GDSII file with sanitization and size check."""
    cleanup_expired_sessions()
    safe_filename = Path(file.filename or "layout.gds").name
    if not (safe_filename.lower().endswith(".gds") or safe_filename.lower().endswith(".gds2")):
        raise HTTPException(status_code=400, detail="Only GDSII (.gds, .gds2) files are supported")

    file_id = str(uuid.uuid4())
    saved_path = UPLOAD_DIR / f"{file_id}.gds"

    total_size = 0
    with open(saved_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_SIZE_BYTES:
                buffer.close()
                if saved_path.exists():
                    saved_path.unlink()
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds maximum upload size limit of {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB",
                )
            buffer.write(chunk)

    try:
        layout = GDSLayout(str(saved_path), tech="default")
    except Exception as e:
        if saved_path.exists():
            saved_path.unlink()
        raise HTTPException(status_code=400, detail=f"Failed to parse GDSII file: {str(e)}")

    SESSION_STORE[file_id] = {
        "gds_path": str(saved_path),
        "layout": layout,
        "filename": safe_filename,
        "tech": "default",
        "guess_layers": False,
        "created_at": time.time(),
    }

    return {
        "file_id": file_id,
        "filename": safe_filename,
        "tech": "default",
        "summary": layout.get_summary(),
    }


@app.post("/api/analyze")
def run_analysis_endpoint(req: AnalysisRequest):
    """Executes PDN mesh extraction, sparse solver, margin verification, and visualization."""
    if req.file_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session expired or file not found. Please upload again.")

    session = SESSION_STORE[req.file_id]
    if req.tech != session.get("tech") or req.guess_layers != session.get("guess_layers", False):
        layout = GDSLayout(session["gds_path"], tech=req.tech, guess_layers=req.guess_layers)
        session["layout"] = layout
        session["tech"] = req.tech
        session["guess_layers"] = req.guess_layers
    else:
        layout = session["layout"]

    # Convert layer_overrides keys to int
    overrides = {}
    if req.layer_overrides:
        for k, v in req.layer_overrides.items():
            try:
                overrides[int(k)] = v
            except ValueError:
                pass

    res = int(req.grid_resolution)
    builder = PDNBuilder(
        layout,
        grid_resolution=(res, res),
        layer_overrides=overrides,
        allow_default_pads=req.allow_default_pads,
        target_net=req.target_net,
        net_layers=req.net_layers,
    )
    network = builder.build_network()

    solver = IRDropSolver(network)
    solver_result = solver.solve(
        v_nom=req.v_nom,
        total_current=req.total_current,
        distribution=req.distribution,
        solver_method=req.solver_method,
    )

    analyzer = IRDropAnalyzer(
        solver_result,
        delta_v_limit_mv=req.limit_mv,
        max_violation_rows=req.max_violation_rows,
    )
    analysis = analyzer.analyze()

    visualizer = IRDropVisualizer(layout, solver_result, analysis)

    # Generate Image based on heatmap_mode
    if req.heatmap_mode == "3d_surface":
        fig_heatmap = visualizer.generate_3d_surface_figure(cmap_name=req.cmap_name)
    elif req.heatmap_mode == "histogram":
        fig_heatmap = visualizer.generate_histogram_figure()
    elif req.heatmap_mode == "current_flow":
        fig_heatmap = visualizer.generate_current_flow_figure()
    else:
        fig_heatmap = visualizer.generate_heatmap_figure(
            mode=req.heatmap_mode,
            show_layout_overlay=req.show_layout_overlay,
            show_contours=req.show_contours,
            show_pads=req.show_pads,
            show_worst_marker=req.show_worst_marker,
            cmap_name=req.cmap_name,
        )
    heatmap_b64 = visualizer.to_base64_png(fig_heatmap)

    # Generate Cutline
    fig_cutline = visualizer.generate_cutline_figure()
    cutline_b64 = visualizer.to_base64_png(fig_cutline)

    # Cache simulation result for interactive cutlines & probes
    session["solver_result"] = solver_result
    session["analysis"] = analysis
    session["visualizer"] = visualizer
    session["heatmap_b64"] = heatmap_b64
    session["cutline_b64"] = cutline_b64
    session["builder"] = builder
    session["network"] = network
    session["req"] = req

    # Downsampled grid for client-side cursor probe (e.g. 50x50)
    stride = max(1, res // 50)
    probe_x = solver_result.x_coords_um[::stride].tolist()
    probe_y = solver_result.y_coords_um[::stride].tolist()
    probe_drop_mv = solver_result.smoothed_ir_drop_mv[::stride, ::stride].tolist()

    return {
        "analysis": {
            "v_nom": analysis.v_nom,
            "delta_v_limit_mv": analysis.delta_v_limit_mv,
            "min_allowed_voltage_v": analysis.min_allowed_voltage_v,
            "delta_v_max_mv": analysis.delta_v_max_mv,
            "min_observed_voltage_v": analysis.min_observed_voltage_v,
            "delta_v_avg_mv": analysis.delta_v_avg_mv,
            "delta_v_std_mv": analysis.delta_v_std_mv,
            "p90_drop_mv": analysis.p90_drop_mv,
            "p95_drop_mv": analysis.p95_drop_mv,
            "p99_drop_mv": analysis.p99_drop_mv,
            "margin_mv": analysis.margin_mv,
            "margin_percentage": analysis.margin_percentage,
            "status": analysis.status,
            "is_safe": analysis.is_safe,
            "worst_node": analysis.worst_node,
            "violating_node_count": analysis.violating_node_count,
            "total_active_nodes": analysis.total_active_nodes,
            "violating_area_percentage": analysis.violating_area_percentage,
            "violating_area_um2": analysis.violating_area_um2,
            "total_active_area_um2": analysis.total_active_area_um2,
            "max_safe_current_ma": analysis.max_safe_current_ma,
            "current_headroom_ma": analysis.current_headroom_ma,
            "max_safe_power_w": analysis.max_safe_power_w,
            "current_power_w": analysis.current_power_w,
            "effective_pdn_resistance_ohm": analysis.effective_pdn_resistance_ohm,
            "peak_pdn_resistance_ohm": analysis.peak_pdn_resistance_ohm,
            "hotspots": [
                {
                    "id": h.id,
                    "bbox_um": h.bbox_um,
                    "center_um": h.center_um,
                    "area_um2": h.area_um2,
                    "max_drop_mv": h.max_drop_mv,
                    "avg_drop_mv": h.avg_drop_mv,
                    "violation_severity_mv": h.violation_severity_mv,
                }
                for h in analysis.hotspots
            ],
            "layer_metrics": analysis.layer_metrics,
            "histogram_bins_mv": analysis.histogram_bins_mv,
            "histogram_counts": analysis.histogram_counts,
            "violating_nodes": analysis.violating_nodes,
        },
        "heatmap_image": heatmap_b64,
        "cutline_image": cutline_b64,
        "solve_time_seconds": round(solver_result.solve_time_seconds, 3),
        "probe_grid": {
            "x": probe_x,
            "y": probe_y,
            "drop_mv": probe_drop_mv,
        },
    }


@app.post("/api/cutline")
def get_custom_cutline(req: CutlineRequest):
    """Generates 1D cross-section cutline at user-chosen Y coordinate."""
    if req.file_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session expired")

    session = SESSION_STORE[req.file_id]
    if "visualizer" not in session:
        raise HTTPException(status_code=400, detail="Run analysis first")

    visualizer: IRDropVisualizer = session["visualizer"]
    fig = visualizer.generate_cutline_figure(cutline_y_um=req.cutline_y_um)
    b64 = visualizer.to_base64_png(fig)
    return {"cutline_image": b64}


@app.get("/api/export-report/{file_id}")
def export_report(file_id: str):
    """Exports full JSON signoff report."""
    if file_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")

    session = SESSION_STORE[file_id]
    if "analysis" not in session:
        raise HTTPException(status_code=400, detail="Run analysis first")

    analysis: MarginAnalysisResult = session["analysis"]
    builder = session.get("builder")
    network = session.get("network")
    solver_result = session.get("solver_result")
    req = session.get("req")

    pad_source = "GDS geometry (pad layers/labels)"
    if analysis.warnings and any("No power pads detected" in w for w in analysis.warnings):
        pad_source = "Synthesized (default peripheral & center)"

    layer_resistances = {}
    if builder:
        for l_id, l_info in builder.layers.items():
            layer_resistances[str(l_id)] = {
                "name": l_info.name,
                "role": l_info.role,
                "sheet_resistance": l_info.sheet_resistance,
                "via_resistance": l_info.via_resistance,
            }

    provenance = {
        "tech_file": session.get("tech", "default"),
        "grid_resolution": [solver_result.grid_resolution[1], solver_result.grid_resolution[0]] if solver_result else None,
        "cell_size_um": [solver_result.cell_width_um, solver_result.cell_height_um] if solver_result else None,
        "solver_method": solver_result.method_used if solver_result else None,
        "solver_iterations": solver_result.iterations if solver_result else None,
        "pad_count": len(network.pad_node_indices) if network else None,
        "pad_source": pad_source,
        "distribution_profile": req.distribution if req else None,
        "layer_resistances": layer_resistances,
        "warnings": analysis.warnings,
    }

    report = {
        "project": "GDSII IR-Drop Physical Estimator",
        "file": session["filename"],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "nominal_voltage_v": analysis.v_nom,
        "drop_limit_mv": analysis.delta_v_limit_mv,
        "min_allowed_voltage_v": analysis.min_allowed_voltage_v,
        "max_ir_drop_mv": analysis.delta_v_max_mv,
        "min_observed_voltage_v": analysis.min_observed_voltage_v,
        "precise_margin_mv": analysis.margin_mv,
        "margin_percentage": analysis.margin_percentage,
        "status": analysis.status,
        "is_safe": analysis.is_safe,
        "worst_hotspot": analysis.worst_node,
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
        "layer_breakdown": analysis.layer_metrics,
        "warnings": analysis.warnings,
        "statistics": {
            "mean_drop_mv": analysis.delta_v_avg_mv,
            "std_dev_mv": analysis.delta_v_std_mv,
            "p90_mv": analysis.p90_drop_mv,
            "p95_mv": analysis.p95_drop_mv,
            "p99_mv": analysis.p99_drop_mv,
        },
        "provenance": provenance,
    }
    return JSONResponse(
        content=report,
        headers={"Content-Disposition": f"attachment; filename={session['filename']}_irdrop_report.json"},
    )


@app.get("/api/export-csv/{file_id}")
def export_csv(file_id: str):
    """Exports CSV table of violating nodes and summary metrics."""
    if file_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")

    session = SESSION_STORE[file_id]
    if "analysis" not in session:
        raise HTTPException(status_code=400, detail="Run analysis first")

    analysis: MarginAnalysisResult = session["analysis"]
    solver_result = session.get("solver_result")
    network = session.get("network")
    req = session.get("req")

    pad_source = "GDS geometry"
    if analysis.warnings and any("No power pads detected" in w for w in analysis.warnings):
        pad_source = "Synthesized"

    grid_str = (
        f"{solver_result.grid_resolution[1]}x{solver_result.grid_resolution[0]} "
        f"({solver_result.cell_width_um:.2f}x{solver_result.cell_height_um:.2f} um/cell)"
        if solver_result
        else "N/A"
    )

    csv_lines = [
        "# VoltDrop GDSII - IR-Drop Estimation Report CSV",
        f"# File: {session['filename']}",
        f"# Nominal Voltage (V): {analysis.v_nom}",
        f"# Tolerance Limit (mV): {analysis.delta_v_limit_mv}",
        f"# Status: {analysis.status}",
        f"# Precise Margin (mV): {analysis.margin_mv}",
        f"# Margin Slack (%): {analysis.margin_percentage}",
        f"# Max Safe Current (mA): {analysis.max_safe_current_ma}",
        f"# Max Safe Power (W): {analysis.max_safe_power_w}",
        f"# Peak IR-Drop (mV): {analysis.delta_v_max_mv}",
        f"# Min Observed Voltage (V): {analysis.min_observed_voltage_v}",
        f"# Tech File: {session.get('tech', 'default')}",
        f"# Grid Resolution: {grid_str}",
        f"# Solver Method: {solver_result.method_used if solver_result else 'N/A'}",
        f"# Pad Count: {len(network.pad_node_indices) if network else 'N/A'} ({pad_source})",
        f"# Current Distribution: {req.distribution if req else 'uniform'}",
        "",
        "Node_Index,X_um,Y_um,IR_Drop_mV,Voltage_V,Margin_Slack_mV",
    ]

    for idx, node in enumerate(analysis.violating_nodes, start=1):
        csv_lines.append(
            f"{idx},{node['x_um']},{node['y_um']},{node['drop_mv']},{node['voltage_v']},{node['margin_mv']}"
        )

    if not analysis.violating_nodes:
        # If no violations, include worst node
        wn = analysis.worst_node
        csv_lines.append(f"1,{wn['x_um']},{wn['y_um']},{wn['drop_mv']},{wn['voltage_v']},{analysis.margin_mv}")

    csv_content = "\n".join(csv_lines)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={session['filename']}_violations.csv"},
    )


@app.get("/api/export-html-report/{file_id}", response_class=HTMLResponse)
def export_html_report(file_id: str):
    """Exports full executive dark terminal signoff certificate report (printable to PDF)."""
    if file_id not in SESSION_STORE:
        raise HTTPException(status_code=404, detail="Session not found")

    session = SESSION_STORE[file_id]
    if "analysis" not in session:
        raise HTTPException(status_code=400, detail="Run analysis first")

    analysis = session["analysis"]
    solver_result = session.get("solver_result")
    network = session.get("network")
    builder = session.get("builder")
    req = session.get("req")

    filename = html.escape(session["filename"])
    heatmap_b64 = session.get("heatmap_b64", "")
    cutline_b64 = session.get("cutline_b64", "")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    pad_source = "GDS geometry (pad layers/labels)"
    if analysis.warnings and any("No power pads detected" in w for w in analysis.warnings):
        pad_source = "Synthesized (default peripheral & center fallback)"

    if solver_result:
        res_str = f"{solver_result.grid_resolution[1]} &times; {solver_result.grid_resolution[0]} ({solver_result.cell_width_um:.2f} &times; {solver_result.cell_height_um:.2f} µm/cell)"
        if solver_result.iterations is not None:
            solver_str = f"{solver_result.method_used.upper()} ({solver_result.iterations} iterations, residual {solver_result.residual:.2e}) in {solver_result.solve_time_seconds:.3f}s"
        else:
            solver_str = f"{solver_result.method_used.upper()} (direct factorized solve) in {solver_result.solve_time_seconds:.3f}s"
    else:
        res_str = "N/A"
        solver_str = "N/A"

    pad_str = f"{len(network.pad_node_indices)} nodes &mdash; {pad_source}" if network else "N/A"
    dist_str = f"{req.distribution} (Total: {req.total_current * 1e3:.1f} mA)" if req else "N/A"
    tech_str = html.escape(session.get("tech", "default"))

    layer_res_list = []
    if builder:
        for lid in sorted(builder.layers.keys()):
            linfo = builder.layers[lid]
            if linfo.role == "metal":
                layer_res_list.append(f"L{lid} ({linfo.name}): {linfo.sheet_resistance:.3g} &Omega;/sq")
            elif linfo.role == "via":
                layer_res_list.append(f"L{lid} ({linfo.name}): {linfo.via_resistance:.3g} &Omega;")
    layer_res_str = ", ".join(layer_res_list) if layer_res_list else "Default synthetic values"

    if analysis.status == "INVALID":
        status_class = "violation"
        status_label = "ESTIMATION INVALID (BLOCKING WARNINGS)"
    elif analysis.is_safe:
        status_class = "pass"
        status_label = "ESTIMATION APPROVED (PASS)"
    else:
        status_class = "violation"
        status_label = "ESTIMATION VIOLATION (FAIL)"

    margin_sign = "+" if analysis.margin_mv >= 0 else ""

    warning_box = ""
    if analysis.warnings:
        warning_box = """
        <div style="background: rgba(255, 170, 0, 0.08); border: 1px solid #ffaa00; border-radius: 4px; padding: 12px 16px; margin-bottom: 20px;">
            <div style="color: #ffaa00; font-weight: 700; margin-bottom: 6px;">[ESTIMATION INTEGRITY WARNINGS]</div>
            <ul style="margin: 0; padding-left: 18px; color: #f1f5f9; font-size: 12px;">
        """ + "".join(f"<li>{html.escape(w)}</li>" for w in analysis.warnings) + "</ul></div>"

    if analysis.current_headroom_ma is not None:
        headroom_str = f"{'+' if analysis.current_headroom_ma >= 0 else ''}{analysis.current_headroom_ma:.1f} mA"
        safe_budget_str = f"Safe Budget: {analysis.max_safe_current_ma:.1f} mA"
    else:
        headroom_str = "N/A"
        safe_budget_str = "Safe Budget: N/A (zero drop)"

    hotspot_rows = ""
    for h in analysis.hotspots:
        hotspot_rows += f"""
        <tr>
            <td>#{h.id}</td>
            <td>({h.center_um[0]:.1f}, {h.center_um[1]:.1f}) µm</td>
            <td>[{h.bbox_um[0]}, {h.bbox_um[1]}] to [{h.bbox_um[2]}, {h.bbox_um[3]}]</td>
            <td>{h.area_um2:.1f} µm²</td>
            <td style="color:#ff334b; font-weight:bold;">{h.max_drop_mv:.2f} mV</td>
            <td>+{h.violation_severity_mv:.1f} mV</td>
        </tr>
        """
    if not hotspot_rows:
        hotspot_rows = "<tr><td colspan='6' style='text-align:center; color:#00ff88;'>Zero violations detected across all active PDN nodes.</td></tr>"

    layer_rows = ""
    for lm in analysis.layer_metrics:
        l_status_col = "#00ff88" if lm['margin_mv'] >= 0 else "#ff334b"
        l_sign = "+" if lm['margin_mv'] >= 0 else ""
        layer_rows += f"""
        <tr>
            <td><b>Layer {lm['layer']}</b></td>
            <td>{lm['max_drop_mv']:.2f} mV</td>
            <td>{lm['avg_drop_mv']:.2f} mV</td>
            <td style="color:{l_status_col}; font-weight:bold;">{l_sign}{lm['margin_mv']:.2f} mV</td>
            <td style="color:{l_status_col}; font-weight:bold;">{lm['status']}</td>
        </tr>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>VoltDrop GDSII - Estimation Certificate [{filename}]</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; font-family: 'JetBrains Mono', Consolas, monospace; }}
        body {{ background: #03060d; color: #f1f5f9; margin: 0; padding: 30px; font-size: 13px; }}
        .header {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #1a273b; padding-bottom: 20px; margin-bottom: 25px; }}
        .title {{ font-size: 20px; font-weight: 800; color: #00f2fe; margin-bottom: 4px; }}
        .meta {{ font-size: 11px; color: #64748b; }}
        .stamp {{ padding: 10px 24px; font-size: 14px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; border-radius: 4px; border: 2px solid; }}
        .stamp.pass {{ background: rgba(0, 255, 136, 0.1); color: #00ff88; border-color: #00ff88; box-shadow: 0 0 15px rgba(0, 255, 136, 0.2); }}
        .stamp.violation {{ background: rgba(255, 51, 75, 0.1); color: #ff334b; border-color: #ff334b; box-shadow: 0 0 15px rgba(255, 51, 75, 0.2); }}
        .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 25px; }}
        .card {{ background: #080d18; border: 1px solid #172233; padding: 12px 14px; border-radius: 4px; }}
        .card-lbl {{ font-size: 10px; color: #64748b; text-transform: uppercase; margin-bottom: 4px; font-weight: 600; }}
        .card-val {{ font-size: 18px; font-weight: 800; color: #f1f5f9; }}
        .card-val.pass {{ color: #00ff88; }}
        .card-val.violation {{ color: #ff334b; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; background: #080d18; border: 1px solid #172233; }}
        th {{ background: #0d1526; text-align: left; padding: 8px 12px; font-size: 11px; color: #00f2fe; border-bottom: 1px solid #172233; text-transform: uppercase; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #111a2b; font-size: 11px; }}
        .images-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 25px; }}
        .img-card {{ background: #080d18; border: 1px solid #172233; padding: 12px; border-radius: 4px; }}
        .img-card img {{ width: 100%; display: block; border-radius: 4px; }}
        .no-print-bar {{ display: flex; justify-content: flex-end; gap: 12px; margin-bottom: 20px; }}
        .btn-print {{ background: #00f2fe; color: #03060d; font-weight: 800; border: none; padding: 8px 18px; cursor: pointer; border-radius: 4px; font-family: inherit; }}
        @media print {{
            .no-print-bar {{ display: none; }}
            body {{ background: #ffffff !important; color: #000000 !important; }}
            .card {{ background: #f8fafc !important; border: 1px solid #cbd5e1 !important; color: #000 !important; }}
            .card-val {{ color: #000 !important; }}
            table {{ background: #fff !important; color: #000 !important; border: 1px solid #cbd5e1 !important; }}
            th {{ background: #f1f5f9 !important; color: #000 !important; }}
            td {{ color: #000 !important; }}
        }}
    </style>
</head>
<body>
    <div class="no-print-bar">
        <button class="btn-print" onclick="window.print()">PRINT / SAVE AS PDF</button>
    </div>

    <div class="header">
        <div>
            <div class="title">&gt; VOLTDROP_GDSII :: ESTIMATION REPORT</div>
            <div class="meta">PHYSICAL PDN EXTRACTION • PRECISE VALUE MARGIN VERIFICATION</div>
            <div class="meta" style="margin-top:6px;">Layout: <b>{filename}</b> | Verified: {timestamp}</div>
        </div>
        <div class="stamp {status_class}">
            {status_label}
        </div>
    </div>

    {warning_box}

    <div class="grid">
        <div class="card">
            <div class="card-lbl">Precise Value Margin</div>
            <div class="card-val {status_class}">{margin_sign}{analysis.margin_mv:.2f} mV</div>
            <div class="meta">Slack: {margin_sign}{analysis.margin_percentage:.1f}%</div>
        </div>
        <div class="card">
            <div class="card-lbl">Worst-Case IR Drop</div>
            <div class="card-val">{analysis.delta_v_max_mv:.2f} mV</div>
            <div class="meta">Limit: {analysis.delta_v_limit_mv:.1f} mV</div>
        </div>
        <div class="card">
            <div class="card-lbl">Min Observed Voltage</div>
            <div class="card-val">{analysis.min_observed_voltage_v:.4f} V</div>
            <div class="meta">Nominal: {analysis.v_nom:.2f} V</div>
        </div>
        <div class="card">
            <div class="card-lbl">Safe Current Headroom</div>
            <div class="card-val {status_class}">{headroom_str}</div>
            <div class="meta">{safe_budget_str}</div>
        </div>
    </div>

    <div class="images-row">
        <div class="img-card">
            <div class="card-lbl" style="margin-bottom:8px;">Physical IR-Drop 2D Heatmap</div>
            <img src="{heatmap_b64}" alt="IR Drop Heatmap">
        </div>
        <div class="img-card">
            <div class="card-lbl" style="margin-bottom:8px;">Worst-Slice 1D Voltage Cutline</div>
            <img src="{cutline_b64}" alt="1D Cutline">
        </div>
    </div>

    <div class="card-lbl" style="margin-bottom:6px;">Analysis Provenance &amp; Verification Environment</div>
    <table>
        <thead>
            <tr>
                <th style="width: 30%;">Parameter</th>
                <th>Configuration / Value</th>
            </tr>
        </thead>
        <tbody>
            <tr><td><b>Technology Mapping</b></td><td>{tech_str}</td></tr>
            <tr><td><b>Discretization Resolution</b></td><td>{res_str}</td></tr>
            <tr><td><b>Linear Solver</b></td><td>{solver_str}</td></tr>
            <tr><td><b>Power Pad Boundaries</b></td><td>{pad_str}</td></tr>
            <tr><td><b>Current Profile</b></td><td>{dist_str}</td></tr>
            <tr><td><b>Layer Resistances</b></td><td style="font-size:11px; word-break:break-all;">{layer_res_str}</td></tr>
        </tbody>
    </table>

    <div class="card-lbl" style="margin-bottom:6px;">Hotspot Region Breakdown & Pinpoint</div>
    <table>
        <thead>
            <tr>
                <th>Hotspot ID</th>
                <th>Center (X, Y)</th>
                <th>Bounding Box (µm)</th>
                <th>Area</th>
                <th>Max Drop</th>
                <th>Over Limit</th>
            </tr>
        </thead>
        <tbody>
            {hotspot_rows}
        </tbody>
    </table>

    <div class="card-lbl" style="margin-bottom:6px;">Layer Stack Resistance & Drop Breakdown</div>
    <table>
        <thead>
            <tr>
                <th>Metal Tier</th>
                <th>Max Drop (mV)</th>
                <th>Avg Drop (mV)</th>
                <th>Layer Margin Slack</th>
                <th>Estimation Status</th>
            </tr>
        </thead>
        <tbody>
            {layer_rows}
        </tbody>
    </table>

    <div style="text-align:center; font-size:10px; color:#64748b; margin-top:20px; border-top:1px solid #172233; padding-top:15px;">
        VoltDrop GDSII Estimation Report
    </div>
</body>
</html>"""
    return HTMLResponse(content=html_content)



# Mount static files
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def run_server(host: str = "127.0.0.1", port: int = 8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
