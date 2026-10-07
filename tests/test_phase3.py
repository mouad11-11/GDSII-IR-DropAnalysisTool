import json
import pytest
from pathlib import Path

from irdrop import GDSLayout, run_analysis
from irdrop.tech import TechConfig, load_tech_file


def test_tech_file_loads():
    # 1. Default / Generic tech file
    generic_tech = load_tech_file("default")
    assert generic_tech is not None
    assert generic_tech.name in ("default", "generic")
    assert (1, 0) in generic_tech.layers
    assert generic_tech.layers[(1, 0)].role == "metal"
    assert generic_tech.layers[(2, 0)].role == "via"

    # 2. IHP SG13G2 tech file
    ihp_tech = load_tech_file("ihp_sg13g2")
    assert ihp_tech is not None
    assert ihp_tech.name == "ihp_sg13g2"
    assert (8, 0) in ihp_tech.layers
    assert ihp_tech.layers[(8, 0)].name == "Metal1"
    assert ihp_tech.layers[(8, 0)].role == "metal"
    assert ihp_tech.layers[(19, 0)].name == "Via1"
    assert ihp_tech.layers[(19, 0)].role == "via"
    assert (67, 0) in ihp_tech.layers
    assert ihp_tech.layers[(67, 0)].name == "TopMetal1"


def test_ihp_layers_classify_correctly():
    layout = GDSLayout("samples/ihp/inverter_top.gds", tech="ihp_sg13g2")
    
    # Check that Metal1 (8, 0) is classified as metal, not pad
    m1_info = layout.get_layer_info(8, 0)
    assert m1_info is not None
    assert m1_info.role == "metal"
    assert m1_info.metal_index == 0

    # Check Via1 (19, 0)
    v1_info = layout.get_layer_info(19, 0)
    assert v1_info is not None
    assert v1_info.role == "via"

    # Check TopMetal1 (67, 0)
    top_info = layout.get_layer_info(67, 0)
    assert top_info is not None
    assert top_info.role == "metal"
    # Even though label VDD is on layer 67, layer 67 must NOT be reclassified as pad role
    assert top_info.role == "metal"

    # Datatype 2 (pins) should be skipped from geometry extraction
    assert (8, 2) not in layout.polygons_by_key
    assert (67, 2) not in layout.polygons_by_key


def test_via_cut_counting():
    layout = GDSLayout("samples/mesh_pdn.gds", tech="default")
    via_counts = layout.get_via_counts(2, (100, 100))
    assert via_counts.shape == (100, 100)
    assert via_counts.sum() > 0
    # In mesh PDN, via cuts are 1 per cell at 100x100 resolution
    assert via_counts.max() == 1.0


def test_pad_label_word_boundary_no_reclassification():
    layout = GDSLayout("samples/ihp/inverter_top.gds", tech="ihp_sg13g2")
    # Pad labels must match VDD/VCC with word boundary and not match vin, vout, pmos, nmos
    pad_texts = [p["text"] for p in layout.pad_labels]
    assert "VDD" in pad_texts
    assert "vin" not in pad_texts
    assert "vout" not in pad_texts
    assert "pmos" not in pad_texts
    assert "nmos" not in pad_texts
    assert "sub!" not in pad_texts


def test_guess_layers_and_warnings():
    # If tech is None and guess_layers is False, raise ValueError
    with pytest.raises(ValueError, match="No tech configuration provided"):
        GDSLayout("samples/mesh_pdn.gds", tech=None, guess_layers=False)

    # If tech is None and guess_layers is True, warn and guess
    layout_guessed = GDSLayout("samples/mesh_pdn.gds", tech=None, guess_layers=True)
    assert any("guessing" in w.lower() for w in layout_guessed.warnings)
    assert layout_guessed.layers[1].role == "metal"
    assert layout_guessed.layers[2].role == "via"


def test_synthetic_samples_match_baseline_with_tech():
    for name, sample in [
        ("mesh_pdn", "samples/mesh_pdn.gds"),
        ("hierarchical_pdn", "samples/hierarchical_pdn.gds"),
        ("bottleneck_pdn", "samples/bottleneck_pdn.gds"),
    ]:
        with open(f"baseline/{name}.json") as f:
            baseline = json.load(f)

        layout, result, analysis, _ = run_analysis(
            sample,
            v_nom=1.0,
            delta_v_limit_mv=50.0,
            total_current=0.4,
            distribution="uniform",
            grid_resolution=(100, 100),
            tech="default",
        )

        assert analysis.status == baseline["status"], f"Failed status match on {name}"
        assert pytest.approx(analysis.delta_v_max_mv, abs=0.1) == baseline["delta_v_max_mv"], f"Failed max drop on {name}"
        assert pytest.approx(analysis.margin_mv, abs=0.1) == baseline["precise_margin_mv"], f"Failed margin on {name}"
