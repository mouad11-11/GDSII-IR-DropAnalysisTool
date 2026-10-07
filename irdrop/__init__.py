"""
IR-Drop Analysis Package for GDSII Layouts.
"""

from irdrop.gds_parser import GDSLayout, LayerInfo
from irdrop.pdn_model import PDNBuilder, PDNNetwork
from irdrop.solver import IRDropSolver, SolverResult
from irdrop.analyzer import IRDropAnalyzer, MarginAnalysisResult, HotspotRegion
from irdrop.visualizer import IRDropVisualizer

def run_analysis(
    gds_path: str,
    v_nom: float = 1.0,
    delta_v_limit_mv: float = 50.0,
    total_current: float = 0.4,
    distribution: str = "uniform",
    grid_resolution: tuple[int, int] = (100, 100),
    layer_overrides: dict = None,
    allow_default_pads: bool = False,
) -> tuple[GDSLayout, SolverResult, MarginAnalysisResult, IRDropVisualizer]:
    """Runs end-to-end IR-drop and precise margin analysis on a GDSII file."""
    layout = GDSLayout(gds_path)
    builder = PDNBuilder(
        layout,
        grid_resolution=grid_resolution,
        layer_overrides=layer_overrides,
        allow_default_pads=allow_default_pads,
    )
    network = builder.build_network()
    solver = IRDropSolver(network)
    result = solver.solve(v_nom=v_nom, total_current=total_current, distribution=distribution)
    analyzer = IRDropAnalyzer(result, delta_v_limit_mv=delta_v_limit_mv)
    analysis = analyzer.analyze()
    visualizer = IRDropVisualizer(layout, result, analysis)
    return layout, result, analysis, visualizer

__all__ = [
    "GDSLayout",
    "LayerInfo",
    "PDNBuilder",
    "PDNNetwork",
    "IRDropSolver",
    "SolverResult",
    "IRDropAnalyzer",
    "MarginAnalysisResult",
    "HotspotRegion",
    "IRDropVisualizer",
    "run_analysis",
]
