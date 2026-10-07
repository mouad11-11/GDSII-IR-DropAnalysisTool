"""Tests for Phase 7: Web hardening, input validation, and override immutability."""

import io
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from web.app import app, MAX_UPLOAD_SIZE_BYTES
from irdrop import GDSLayout, PDNBuilder


@pytest.fixture
def client():
    return TestClient(app)


def test_override_persistence_bug():
    """Verify PDNBuilder does not mutate layout.layers across analyses."""
    layout = GDSLayout("samples/mesh_pdn.gds")
    original_r1 = layout.layers[1].sheet_resistance

    # Run 1: Apply override
    builder1 = PDNBuilder(layout, layer_overrides={1: {"sheet_res": 999.0}})
    assert builder1.layers[1].sheet_resistance == 999.0

    # Original layout.layers must NOT have been permanently mutated
    assert layout.layers[1].sheet_resistance == original_r1

    # Run 2: Subsequent builder without overrides must see original R
    builder2 = PDNBuilder(layout)
    assert builder2.layers[1].sheet_resistance == original_r1


def test_bad_filename_sanitized(client, tmp_path):
    """Verify directory traversal in upload filename is sanitized and saved only by uuid."""
    with open("samples/mesh_pdn.gds", "rb") as f:
        gds_bytes = f.read()

    # Filename attempt with directory traversal
    bad_filename = "../../evil_traversal.gds"
    response = client.post(
        "/api/upload",
        files={"file": (bad_filename, io.BytesIO(gds_bytes), "application/octet-stream")},
    )
    assert response.status_code == 200
    data = response.json()
    file_id = data["file_id"]

    # Verify no file named evil_traversal.gds was created outside upload directory
    assert not Path("evil_traversal.gds").exists()
    assert not Path("../../evil_traversal.gds").exists()

    # The file on disk must ONLY be named by file_id
    uploaded_file = Path("uploads") / f"{file_id}.gds"
    assert uploaded_file.exists()


def test_oversized_upload_rejected(client):
    """Verify uploads exceeding size limit are rejected with 413."""
    oversized_data = b"0" * (MAX_UPLOAD_SIZE_BYTES + 1024)
    response = client.post(
        "/api/upload",
        files={"file": ("huge.gds", io.BytesIO(oversized_data), "application/octet-stream")},
    )
    assert response.status_code == 413


def test_out_of_range_resolution_rejected(client):
    """Verify grid_resolution is capped and validated by Pydantic."""
    # Under minimum
    resp_under = client.post(
        "/api/analyze",
        json={"file_id": "dummy", "grid_resolution": 5},
    )
    assert resp_under.status_code == 422

    # Over maximum cap (e.g. > 500)
    resp_over = client.post(
        "/api/analyze",
        json={"file_id": "dummy", "grid_resolution": 2000},
    )
    assert resp_over.status_code == 422


def test_provenance_in_html_and_json_reports(client):
    """Verify HTML, JSON, and CSV exports include provenance metadata."""
    # Load sample
    load_resp = client.post("/api/load-sample/mesh_pdn")
    assert load_resp.status_code == 200
    file_id = load_resp.json()["file_id"]

    # Run analysis
    an_resp = client.post(
        "/api/analyze",
        json={
            "file_id": file_id,
            "grid_resolution": 30,
            "v_nom": 1.2,
            "total_current": 0.05,
            "limit_mv": 50.0,
            "distribution": "uniform",
            "solver_method": "direct",
            "layer_overrides": {"1": {"sheet_res": 0.08}},
        },
    )
    assert an_resp.status_code == 200

    # JSON export provenance
    json_resp = client.get(f"/api/export-report/{file_id}")
    assert json_resp.status_code == 200
    report_data = json_resp.json()
    assert "provenance" in report_data
    prov = report_data["provenance"]
    assert prov["tech_file"] == "default"
    assert prov["solver_method"] == "direct_spsolve"
    assert "layer_resistances" in prov
    assert "1" in prov["layer_resistances"]
    assert prov["layer_resistances"]["1"]["sheet_resistance"] == 0.08

    # CSV export provenance comments
    csv_resp = client.get(f"/api/export-csv/{file_id}")
    assert csv_resp.status_code == 200
    csv_text = csv_resp.text
    assert "# Tech File: default" in csv_text
    assert "# Grid Resolution: 30x30" in csv_text
    assert "# Solver Method: direct_spsolve" in csv_text

    # HTML export provenance section
    html_resp = client.get(f"/api/export-html-report/{file_id}")
    assert html_resp.status_code == 200
    html_text = html_resp.text
    assert "Analysis Provenance &amp; Verification Environment" in html_text
    assert "Technology Mapping" in html_text
    assert "Discretization Resolution" in html_text
    assert "DIRECT_SPSOLVE" in html_text

