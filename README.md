# VoltDrop GDSII

Static IR-drop analysis and margin signoff verification tool for GDSII layouts.

VoltDrop parses physical layout files (`.gds`, `.gds2`), extracts metal grids and inter-layer via connectivity, constructs a multi-layer resistive conductance network, and solves the system using sparse Modified Nodal Analysis (MNA). It calculates precise voltage drop margins against user-specified limits and generates 2D spatial distributions, 1D cutlines, and signoff reports.

## Features

- **Layout Extraction**: Hierarchical GDSII parsing via `gdstk` with polygon rasterization for metal and via layers.
- **PDN Network Modeling**: 3D resistive mesh modeling with configurable intra-layer sheet resistances ($R_\square$) and via contact resistances ($R_{via}$). Supports Dirichlet boundary conditions on supply pads/C4 bumps and distributed M1 current sinks.
- **Margin Signoff**:
  $$\text{Margin } (\text{mV}) = \Delta V_{\text{limit}} - \Delta V_{\text{max}}$$
  $$\text{Slack Ratio} = \frac{\Delta V_{\text{limit}} - \Delta V_{\text{max}}}{\Delta V_{\text{limit}}} \times 100\%$$
- **Headroom & Budgeting**: Computes maximum safe current budget ($I_{\text{safe}}$), current headroom, and effective PDN resistance ($R_{\text{eff}}$).
- **Visualization**:
  - 2D voltage drop ($\Delta V$) and supply potential ($V$) distributions
  - Margin slack map
  - 1D cross-section cutlines
  - 3D potential surfaces and current flow vector fields ($\vec{J} \propto -\nabla V$)
- **Interfaces**: Browser-based interactive viewer and scriptable CLI for batch automation.

## Installation

Requirements: Python 3.10+

```bash
git clone https://github.com/mouad11-11/GDSII-IR-DropAnalysisTool.git
cd GDSII-IR-DropAnalysisTool
pip install -r requirements.txt
```

## Quick Start

### Web Interface
```bash
python run.py
```
Starts the local server at `http://127.0.0.1:8000`.

### Command-Line Interface
```bash
# Basic run on sample
python cli.py samples/mesh_pdn.gds --vnom 1.0 --limit-mv 40.0 --current 0.35 --dist center_hotspot --output report/

# Run with custom limit and generate CSV/JSON reports
python cli.py samples/bottleneck_pdn.gds --vnom 1.0 --limit-mv 45.0 --current 0.40 --output report/
```

### CLI Arguments

| Argument | Description | Default |
|---|---|---|
| `gds_file` | Path to input GDSII layout | *(required)* |
| `--vnom` | Nominal supply voltage $V_{\text{nom}}$ (V) | `1.0` |
| `--limit-mv` | Voltage drop tolerance limit (mV) | `50.0` |
| `--limit-pct` | Drop limit as percentage of $V_{\text{nom}}$ | `None` |
| `--current` | Total supply current (A) | `0.4` |
| `--dist` | Current distribution profile (`uniform`, `center_hotspot`, `dual_hotspot`, `quad_hotspot`) | `uniform` |
| `--res` | Mesh discretization grid per axis | `100` |
| `--output` | Output directory for figures and reports | `report` |
| `--no-overlay` | Disable layout wire overlay on heatmaps | `False` |
| `--no-contours`| Disable isopotential contour lines | `False` |

### Running Tests
```bash
pytest tests/test_irdrop.py -v
```

## PDN Benchmarks Included

The `samples/` directory contains sample layouts:
- `samples/mesh_pdn.gds`: 2-layer power mesh (M1 rails, M2 distribution stripes, peripheral pads).
- `samples/hierarchical_pdn.gds`: 4-metal layer hierarchical PDN with a 4x4 C4 bump array.
- `samples/bottleneck_pdn.gds`: Mesh with localized missing vias and necked metal lines for margin violation validation.
- `samples/ihp/`: Taped-out open-source silicon macros from the IHP SG13G2 PDK (`counter_top.gds`, `inverter_top.gds`, `sg13g2_ip__bondpad_70x70.gds`).

## Formulation

The static conductance formulation solves:

$$G \cdot V = I$$

where:
- $G$ is the sparse admittance matrix assembled from cell-to-cell conductance values $g_{x} = \frac{1}{R_\square} \frac{\Delta y}{\Delta x}$ and via conductances $g_{v} = \frac{1}{R_{\text{via}}}$.
- Pad nodes $p \in \mathcal{P}$ have Dirichlet conditions $V_p = V_{\text{nom}}$.
- Current sinks $s \in \mathcal{S}$ on standard-cell rails draw current such that $\sum I_s = I_{\text{total}}$.
- Voltage drop at node $k$ is $\Delta V_k = V_{\text{nom}} - V_k$.

## Project Structure

```
.
├── cli.py                  # CLI entrypoint
├── run.py                  # Web application launcher
├── requirements.txt
├── irdrop/                 # Analysis engine
│   ├── gds_parser.py       # GDSII hierarchy extraction & rasterization
│   ├── pdn_model.py        # 3D resistive mesh builder
│   ├── solver.py           # Sparse linear system solver
│   ├── analyzer.py         # Margin calculation & hotspot clustering
│   ├── visualizer.py       # Plot generation
│   └── sample_generator.py # Synthetic benchmark generator
├── web/                    # Web application
│   ├── app.py              # FastAPI server
│   └── static/             # Frontend assets
├── samples/                # Sample GDSII layouts
└── tests/                  # Test suite
```

## License

MIT License.
