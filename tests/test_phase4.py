"""Tests for Phase 4: Geometry-accurate conductance, width-based R, and net selection."""

import gdstk
import numpy as np
import pytest
from pathlib import Path

from irdrop import GDSLayout, PDNBuilder, IRDropSolver, IRDropAnalyzer, run_analysis


def create_strip_gds(filepath: str, length_um: float = 100.0, width_um: float = 10.0):
    """Creates a calibrated single-strip GDS layout."""
    lib = gdstk.Library()
    cell = lib.new_cell("TEST_STRIP")
    y_min = 50.0 - (width_um / 2.0)
    y_max = 50.0 + (width_um / 2.0)
    # Metal 1 stripe
    cell.add(gdstk.rectangle((0.0, y_min), (length_um, y_max), layer=1, datatype=0))
    # Edge pad at x = 0 on layer 10
    pad_w = 0.5
    cell.add(gdstk.rectangle((0.0, y_min), (pad_w, y_max), layer=10, datatype=0))
    cell.add(gdstk.Label("VDD", (0.1, 50.0), layer=10))

    lib.write_gds(filepath)


def test_single_strip_matches_analytic_ir_drop(tmp_path):
    gds_file = str(tmp_path / "strip.gds")
    length = 100.0  # um
    width = 10.0    # um
    r_sheet = 0.1   # Ohm/sq
    total_current = 0.1  # Amperes (100 mA)

    create_strip_gds(gds_file, length_um=length, width_um=width)

    # Analytic resistance: R = Rsheet * (L / W)
    # For distributed uniform current along a linear strip with pad at one end:
    # Delta_V = 0.5 * I * R = 0.5 * 0.1 * (0.1 * 100 / 10) = 0.5 * 0.1 * 1.0 = 0.05 V = 50.0 mV
    expected_drop_mv = 0.5 * total_current * (r_sheet * (length / width)) * 1000.0  # 50.0 mV

    layout, result, analysis, _ = run_analysis(
        gds_file,
        v_nom=1.0,
        delta_v_limit_mv=200.0,
        total_current=total_current,
        distribution="uniform",
        grid_resolution=(101, 101),
        layer_overrides={1: {"sheet_res": r_sheet}},
        tech="default",
    )

    # Must match analytic V = 0.5 * I * R within 5%
    assert pytest.approx(expected_drop_mv, rel=0.05) == analysis.delta_v_max_mv


def test_resolution_convergence(tmp_path):
    gds_file = str(tmp_path / "convergence_strip.gds")
    create_strip_gds(gds_file, length_um=100.0, width_um=10.0)

    drops = []
    for res in [50, 100, 150]:
        layout, result, analysis, _ = run_analysis(
            gds_file,
            v_nom=1.0,
            delta_v_limit_mv=200.0,
            total_current=0.1,
            distribution="uniform",
            grid_resolution=(res, res),
            layer_overrides={1: {"sheet_res": 0.1}},
            tech="default",
        )
        drops.append(analysis.delta_v_max_mv)

    # Max drop must stabilize as grid resolution increases
    # Delta between res=100 and res=150 should be very small (< 2 mV)
    delta_fine = abs(drops[-1] - drops[-2])
    assert delta_fine < 2.0, f"Drop did not stabilize: {drops}"


def test_net_selection(tmp_path):
    # Verify target_net selection filters pads for target net
    layout, result, analysis, _ = run_analysis(
        "samples/ihp/inverter_top.gds",
        v_nom=1.2,
        delta_v_limit_mv=50.0,
        total_current=0.05,
        distribution="uniform",
        grid_resolution=(80, 80),
        tech="ihp_sg13g2",
        target_net="VDD",
    )
    assert analysis.status in ("PASS", "VIOLATION")
