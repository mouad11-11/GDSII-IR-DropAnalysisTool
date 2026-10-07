import gdstk
from pathlib import Path
import pytest

from irdrop import GDSLayout, PDNBuilder, IRDropSolver, IRDropAnalyzer, run_analysis


@pytest.fixture
def abnormal_gds_files(tmp_path):
    # 1. GDS with no pads (only M1, no pad layer 10 or pad labels)
    no_pads_path = tmp_path / "no_pads.gds"
    lib1 = gdstk.Library("NO_PADS", unit=1e-6, precision=1e-9)
    cell1 = lib1.new_cell("TOP")
    # Only metal 1 rails (layer 1)
    cell1.add(gdstk.rectangle((0, 10), (100, 20), layer=1, datatype=0))
    lib1.write_gds(str(no_pads_path))

    # 2. GDS with no sinks (only Layer 10 pads, no Metal 1)
    no_sinks_path = tmp_path / "no_sinks.gds"
    lib2 = gdstk.Library("NO_SINKS", unit=1e-6, precision=1e-9)
    cell2 = lib2.new_cell("TOP")
    cell2.add(gdstk.rectangle((0, 0), (20, 20), layer=10, datatype=0))
    lib2.write_gds(str(no_sinks_path))

    # 3. GDS with floating load (disconnected island on M1 with no via to pads)
    floating_path = tmp_path / "floating_load.gds"
    lib3 = gdstk.Library("FLOATING", unit=1e-6, precision=1e-9)
    cell3 = lib3.new_cell("TOP")
    # Connected part: Pad on layer 10, M2 on layer 3, Via on layer 2, M1 rail on layer 1
    cell3.add(gdstk.rectangle((0, 0), (20, 20), layer=10, datatype=0))
    cell3.add(gdstk.rectangle((0, 0), (20, 100), layer=3, datatype=0))
    cell3.add(gdstk.rectangle((0, 0), (20, 20), layer=2, datatype=0))
    cell3.add(gdstk.rectangle((0, 0), (50, 20), layer=1, datatype=0))
    # Disconnected island on M1 at x=70..100, y=70..90 (completely isolated from pad at x=0..20)
    cell3.add(gdstk.rectangle((70, 70), (100, 90), layer=1, datatype=0))
    lib3.write_gds(str(floating_path))

    return {
        "no_pads": str(no_pads_path),
        "no_sinks": str(no_sinks_path),
        "floating": str(floating_path),
    }


def test_no_pads_raises_unless_allowed(abnormal_gds_files):
    # Without allow_default_pads, it must raise ValueError
    with pytest.raises(ValueError, match="No power pads"):
        run_analysis(abnormal_gds_files["no_pads"], allow_default_pads=False)

    # With allow_default_pads=True, it should have a warning and status must not be silently clean PASS
    layout, result, analysis, _ = run_analysis(abnormal_gds_files["no_pads"], allow_default_pads=True)
    assert any("default" in w.lower() or "pad" in w.lower() for w in analysis.warnings)


def test_no_sinks_raises(abnormal_gds_files):
    # Must raise ValueError when no valid current sinks exist
    with pytest.raises(ValueError, match="No valid current sinks"):
        run_analysis(abnormal_gds_files["no_sinks"], allow_default_pads=True)


def test_floating_load_fails_signoff(abnormal_gds_files):
    # Must detect disconnected sinks and fail signoff (status INVALID, not PASS)
    layout, result, analysis, _ = run_analysis(
        abnormal_gds_files["floating"],
        allow_default_pads=True,
        total_current=0.1,
    )
    assert analysis.status == "INVALID"
    assert analysis.is_safe is False
    assert any("disconnected" in w.lower() or "floating" in w.lower() for w in analysis.warnings)


def test_warnings_fields_exist():
    layout = GDSLayout("samples/mesh_pdn.gds")
    builder = PDNBuilder(layout, grid_resolution=(20, 20))
    network = builder.build_network()
    assert hasattr(network, "warnings")
    assert isinstance(network.warnings, list)

    solver = IRDropSolver(network)
    result = solver.solve(v_nom=1.0, total_current=0.1)
    assert hasattr(result, "warnings")
    assert isinstance(result.warnings, list)

    analyzer = IRDropAnalyzer(result, delta_v_limit_mv=50.0)
    analysis = analyzer.analyze()
    assert hasattr(analysis, "warnings")
    assert isinstance(analysis.warnings, list)
