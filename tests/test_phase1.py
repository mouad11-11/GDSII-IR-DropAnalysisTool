import subprocess
import sys
import numpy as np
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from irdrop.analyzer import IRDropAnalyzer
from irdrop.solver import SolverResult
from irdrop.gds_parser import GDSLayout
from web.app import app, SESSION_STORE
from irdrop import run_analysis


def test_cli_exit_codes():
    cli_path = Path(__file__).parent.parent / "cli.py"
    
    # 1. Missing file -> exit code 1
    res_missing = subprocess.run([sys.executable, str(cli_path), "nonexistent_layout_file.gds"])
    assert res_missing.returncode == 1

    # 2. Pass -> exit code 0
    res_pass = subprocess.run([
        sys.executable, str(cli_path), "samples/mesh_pdn.gds",
        "--vnom", "1.0", "--limit-mv", "50.0", "--current", "0.4"
    ])
    assert res_pass.returncode == 0

    # 3. Violation -> exit code 2
    res_violation = subprocess.run([
        sys.executable, str(cli_path), "samples/bottleneck_pdn.gds",
        "--vnom", "1.0", "--limit-mv", "50.0", "--current", "0.4"
    ])
    assert res_violation.returncode == 2


def test_html_report_xss_filename():
    client = TestClient(app)
    fake_session_id = "test_xss_session"
    _, _, analysis, _ = run_analysis(
        "samples/mesh_pdn.gds",
        v_nom=1.0,
        delta_v_limit_mv=50.0,
        total_current=0.2,
        grid_resolution=(20, 20),
    )
    
    SESSION_STORE[fake_session_id] = {
        "filename": "<script>alert('xss')</script>.gds",
        "analysis": analysis,
        "heatmap_b64": "",
        "cutline_b64": "",
    }
    
    response = client.get(f"/api/export-html-report/{fake_session_id}")
    assert response.status_code == 200
    # Must NOT contain raw unescaped script tag
    assert "<script>alert('xss')</script>" not in response.text
    # Must contain HTML-escaped characters
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in response.text or "&lt;script&gt;alert('xss')&lt;/script&gt;" in response.text


def test_safe_current_when_drop_is_zero():
    # Construct a SolverResult with zero drop
    dummy_result = SolverResult(
        v_nom=1.0,
        total_current=0.1,
        solve_time_seconds=0.01,
        converged=True,
        layer_voltages={1: np.ones((5, 5))},
        layer_ir_drops={1: np.zeros((5, 5))},
        layer_active_mask={1: np.ones((5, 5), dtype=bool)},
        composite_ir_drop_v=np.zeros((5, 5)),
        smoothed_ir_drop_mv=np.zeros((5, 5)),
        active_die_mask=np.ones((5, 5), dtype=bool),
        x_coords_um=np.linspace(0, 10, 5),
        y_coords_um=np.linspace(0, 10, 5),
    )
    analyzer = IRDropAnalyzer(dummy_result, delta_v_limit_mv=50.0)
    analysis = analyzer.analyze()
    assert analysis.delta_v_max_mv == 0.0
    assert analysis.max_safe_current_ma is None
    assert analysis.current_headroom_ma is None


def test_gds_lib_unit_applied():
    # Load sample layout and verify unit attribute exists and coordinates in bbox match microns
    layout = GDSLayout("samples/mesh_pdn.gds")
    assert layout.unit == 1e-6
    min_x, min_y, max_x, max_y = layout.bbox
    # For mesh_pdn, die size is 200um x 200um
    assert round(max_x - min_x, 1) == 200.0
    assert round(max_y - min_y, 1) == 200.0
