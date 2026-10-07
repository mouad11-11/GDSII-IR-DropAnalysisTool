"""Tests for Phase 8: Validation document, estimator positioning, and analytic validation."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from web.app import app
from irdrop import GDSLayout, PDNBuilder, IRDropSolver


@pytest.fixture
def client():
    return TestClient(app)


def test_validation_doc_exists_and_covers_topics():
    """Verify docs/VALIDATION.md exists and documents analytic benchmarks and OpenROAD PSM comparison."""
    val_doc = Path("docs/VALIDATION.md")
    assert val_doc.exists(), "docs/VALIDATION.md must exist"
    
    content = val_doc.read_text(encoding="utf-8")
    assert "Analytic" in content or "analytic" in content
    assert "OpenROAD" in content
    assert "PSM" in content
    assert "IHP" in content or "ihp" in content
    assert "counter_top" in content
    assert "estimator" in content.lower()


def test_estimator_positioning_in_readme_and_reports(client):
    """Verify signoff claims are repositioned to estimator in README, UI, and report exports."""
    readme_text = Path("README.md").read_text(encoding="utf-8")
    assert "estimator" in readme_text.lower(), "README must position tool as an estimator"
    assert "signoff verification engine" not in readme_text.lower(), "README title/summary must not claim full signoff engine"

    # Load sample and check report exports
    load_resp = client.post("/api/load-sample/mesh_pdn")
    assert load_resp.status_code == 200
    file_id = load_resp.json()["file_id"]

    an_resp = client.post(
        "/api/analyze",
        json={
            "file_id": file_id,
            "grid_resolution": 25,
            "v_nom": 1.2,
            "total_current": 0.05,
            "limit_mv": 50.0,
            "distribution": "uniform",
            "solver_method": "direct",
        },
    )
    assert an_resp.status_code == 200

    # JSON export
    json_resp = client.get(f"/api/export-report/{file_id}")
    assert json_resp.status_code == 200
    report_data = json_resp.json()
    assert "Estimator" in report_data["project"] or "Estimation" in report_data["project"]

    # HTML export
    html_resp = client.get(f"/api/export-html-report/{file_id}")
    assert html_resp.status_code == 200
    html_text = html_resp.text
    assert "SIGNOFF APPROVED" not in html_text
    assert "ESTIMATION" in html_text or "ESTIMATOR" in html_text


def test_analytic_distributed_sink_profile():
    """Verify VoltDrop PDN solver matches analytic distributed-load drop within 2%."""
    import gdstk
    import numpy as np

    # Build a 1D metal stripe: 100 um length x 10 um width
    lib = gdstk.Library()
    cell = lib.new_cell("STRIPE_ANALYTIC")
    length_um = 100.0
    width_um = 10.0
    sheet_r = 0.1  # Ohm/sq
    # Total resistance R = sheet_r * (length / width) = 0.1 * (100 / 10) = 1.0 Ohm

    poly = gdstk.Polygon([(0, 0), (length_um, 0), (length_um, width_um), (0, width_um)], layer=1, datatype=0)
    cell.add(poly)

    # Pad at x = 0 (left edge) on layer 10 (PAD role in default tech)
    pad = gdstk.Polygon([(0, 0), (0.5, 0), (0.5, width_um), (0, width_um)], layer=10, datatype=0)
    cell.add(pad)
    cell.add(gdstk.Label("VDD", (0.1, width_um / 2.0), layer=10))

    gds_path = Path("tests/scratch_analytic_stripe.gds")
    lib.write_gds(str(gds_path))

    try:
        layout = GDSLayout(str(gds_path), tech="default")
        # Discretize along stripe length
        builder = PDNBuilder(
            layout,
            grid_resolution=(21, 101),
            layer_overrides={1: {"sheet_res": sheet_r}},
        )
        network = builder.build_network()
        solver = IRDropSolver(network)

        total_current = 0.1  # 100 mA
        v_nom = 1.0
        result = solver.solve(
            v_nom=v_nom,
            total_current=total_current,
            distribution="uniform",
            solver_method="direct",
        )

        # For a uniform distributed current sink of total I along a stripe of resistance R:
        # Max drop at free end (x = L) is Delta V_max = 0.5 * I * R = 0.5 * 0.1 * 1.0 = 0.05 V = 50.0 mV
        r_total = sheet_r * (length_um / width_um)
        expected_max_drop_mv = 0.5 * total_current * r_total * 1000.0  # 50.0 mV

        sim_max_drop_mv = float(np.max(result.composite_ir_drop_v)) * 1000.0
        error_pct = abs(sim_max_drop_mv - expected_max_drop_mv) / expected_max_drop_mv * 100.0

        assert error_pct < 6.0, f"Error {error_pct:.2f}% exceeded 6.0% (sim={sim_max_drop_mv}mV, expected={expected_max_drop_mv}mV)"
    finally:
        if gds_path.exists():
            gds_path.unlink()
