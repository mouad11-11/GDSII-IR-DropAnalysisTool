# VoltDrop GDSII

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SciPy MNA](https://img.shields.io/badge/SciPy-Sparse_MNA-8CAAE6.svg?style=flat-square&logo=scipy&logoColor=white)](https://scipy.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey.svg?style=flat-square)]()
[![CI](https://github.com/mouad11-11/GDSII-IR-DropAnalysisTool/actions/workflows/ci.yml/badge.svg)](https://github.com/mouad11-11/GDSII-IR-DropAnalysisTool/actions/workflows/ci.yml)

Physical power delivery network (PDN) IR-drop estimator and margin verification tool for integrated circuit layouts.

VoltDrop GDSII directly extracts multi-tier metal interconnects and via arrays from standard GDSII stream files (`.gds`, `.gds2`), builds a 3D resistive mesh with width-aware edge conductances, and solves for node potentials using Modified Nodal Analysis (MNA) with an AMG-preconditioned Conjugate Gradient solver. It evaluates voltage drop margins against user-defined thresholds, clusters spatial hotspots using connected components, and provides both an interactive dark terminal web interface and an automated CLI for regressions.

> **Tool Positioning:** VoltDrop GDSII is an **early-stage physical estimator** intended for rapid PDN screening, floorplan verification, and power strap sanity checks directly on layout polygons. For tapeout signoff comparisons with OpenROAD PSM and analytic benchmarks, refer to [docs/VALIDATION.md](docs/VALIDATION.md).

---

## Table of Contents

- [Pipeline Architecture](#pipeline-architecture)
- [Mathematical Formulation](#mathematical-formulation)
- [Validation & Tool Positioning](#validation--tool-positioning)
- [Features](#features)
- [Benchmark Layouts](#benchmark-layouts)
- [Installation](#installation)
- [Quick Start](#quick-start)
  - [Web Interface](#web-interface)
  - [Command-Line Interface](#command-line-interface)
  - [Python API](#python-api)
- [CLI Reference](#cli-reference)
- [Technology Configuration](#technology-configuration)
- [Test Suite](#test-suite)
- [Repository Structure](#repository-structure)
- [License](#license)

---

## Pipeline Architecture

```
   +-------------------+
   | GDSII Stream File |
   |  (.gds / .gds2)   |
   +---------+---------+
             |
             v
   +-------------------+       +----------------------+
   | Hierarchical      | ----> | 2D Rasterization     |
   | Polygon Extractor |       | Area Coverage Matrix |
   | (gdstk)           |       | (OpenCV)             |
   +-------------------+       +----------+-----------+
                                          |
                                          v
   +-------------------+       +----------------------+
   | Boundary & Source | ----> | 3D Conductance Mesh  |
   | Definitions       |       | Harmonic Width Model |
   | (Pads / C4 / Sinks)       | (gx, gy, gvia)       |
   +-------------------+       +----------+-----------+
                                          |
                                          v
                               +----------------------+
                               | Sparse Linear System |
                               | Dirichlet Reduction  |
                               | AMG-CG / direct      |
                               +----------+-----------+
                                          |
                                          v
                               +----------------------+
                               | Margin Estimation &  |
                               | Hotspot Clustering   |
                               | (Connected Comp.)    |
                               +----------+-----------+
                                          |
                        +-----------------+-----------------+
                        |                                   |
                        v                                   v
             +--------------------+              +--------------------+
             | Pitch-Black Web UI |              | CLI Batch Engine   |
             | Heatmaps, Cutlines |              | Plots, CSV, JSON   |
             | Provenance Report  |              | Automated Exit 0/2 |
             +--------------------+              +--------------------+
```

---

## Mathematical Formulation

### 1. Admittance Matrix Assembly

VoltDrop discretizes metal layers into a 2D grid per tier connected vertically through via layers. Enforcing Kirchhoff's Current Law (KCL) yields the Modified Nodal Analysis (MNA) system:

$$G \mathbf{v} = \mathbf{i}$$

Where:
- $G \in \mathbb{R}^{N \times N}$ is the sparse, symmetric positive-definite conductance matrix.
- $\mathbf{v} \in \mathbb{R}^N$ is the nodal electrical potential vector.
- $\mathbf{i} \in \mathbb{R}^N$ is the current injection and sink vector.

### 2. Geometry-Aware Conductances

Intra-layer conductances account for wire width and partial cell occupancy using harmonic-mean effective conductances between adjacent cells:

$$g_x = \frac{1}{R_\square} \cdot \frac{\Delta y}{\Delta x} \cdot c_{\text{eff}, x}, \quad g_y = \frac{1}{R_\square} \cdot \frac{\Delta x}{\Delta y} \cdot c_{\text{eff}, y}$$

Where $c_{\text{eff}} = \frac{2 \cdot o_1 \cdot o_2}{o_1 + o_2}$ for cells with fractional area coverage $o_1, o_2$.

Inter-layer via conductance between overlapping metal polygons uses explicit via cut counting from the layout or stack adjacency definitions:

$$g_{\text{via}} = \frac{N_{\text{cuts}}}{R_{\text{via}}}$$

### 3. Boundary Conditions & Numerical Solving

- **Dirichlet Boundary Nodes (Pads & Bumps):** Known supply potentials are eliminated into the right-hand side vector:
  $$A_{UU} \mathbf{v}_U = \mathbf{i}_U - G_{UD} \mathbf{v}_D$$
  This preserves the strict symmetric positive-definite (SPD) property of the active submatrix $A_{UU}$.
- **Iterative and Direct Solvers:** Grids with fewer than 5,000 unknowns default to direct sparse factorization (`spsolve`). Larger systems leverage Algebraic Multigrid preconditioned Conjugate Gradient (`pyamg` + `scipy.sparse.linalg.cg`) with automatic residual verification.
- **Current Load Modeling:** Total current $I_{\text{total}}$ is distributed across active bottom-metal rails according to cell area weights or user-defined hotspot bounding boxes.

### 4. Margin Reliability Metrics

- **Nodal IR Drop:**
  $$\Delta V_k = V_{\text{nom}} - V_k$$
- **Absolute Voltage Margin:**
  $$\text{Margin } (\text{mV}) = \Delta V_{\text{limit}} - \Delta V_{\text{max}}$$
  A positive margin indicates compliance (`PASS`), while a negative margin indicates a threshold violation (`VIOLATION`).
- **Margin Slack Ratio:**
  $$\text{Slack Ratio } (\%) = \frac{\Delta V_{\text{limit}} - \Delta V_{\text{max}}}{\Delta V_{\text{limit}}} \times 100\%$$
- **Maximum Safe Current Budget:**
  $$I_{\text{safe}} = I_{\text{total}} \cdot \left( \frac{\Delta V_{\text{limit}}}{\Delta V_{\text{max}}} \right)$$
- **Effective PDN Impedance:**
  $$R_{\text{eff}} = \frac{\Delta V_{\text{avg}}}{I_{\text{total}}}, \quad R_{\text{peak}} = \frac{\Delta V_{\text{max}}}{I_{\text{total}}}$$

---

## Validation & Tool Positioning

VoltDrop GDSII is validated against both closed-form theoretical benchmarks and real silicon macros:

1. **Analytic 1D Distributed Load:** Matches theoretical parabolic potential profile $\Delta V(x) = \frac{I R}{2} (\frac{2x}{L} - \frac{x^2}{L^2})$ within $0.5\%$.
2. **Point-Load Verification:** Verifies Ohm's Law $V = I R$ on calibrated metal stripes within $0.2\%$.
3. **OpenROAD PSM Correlation:** Evaluated against OpenROAD's Power Grid Meter (`analyze_power_grid`) on the open-source IHP SG13G2 digital counter macro (`counter_top.gds`). VoltDrop reproduces peak drop within $\sim 4\%$ and isolates identical core hotspot regions in under $1.5$ seconds ($29\times$ faster than full DEF/LEF/STA extraction).

For detailed equations, convergence sweeps, and tool comparison tables, see [docs/VALIDATION.md](docs/VALIDATION.md).

---

## Features

- **Direct GDSII Stream Parsing:** Ingests raw `.gds` and `.gds2` files using `gdstk` with boundary filtering, word-boundary net label detection, and polygon rasterization.
- **Technology Layer Mapping:** JSON-based PDK definition files mapping `(layer, datatype)` pairs to stack order, sheet resistance, via resistance, and connectivity. Includes built-in support for generic CMOS stacks and IHP SG13G2 130nm BiCMOS.
- **Accelerated Solver Engine:** AMG-preconditioned CG solver with Dirichlet boundary reduction and SciPy direct sparse fallback. Vectorized network building with NumPy slicing.
- **Analysis Consistency:** Violations, spatial hotspots, and slack maps evaluate directly on active physical metal nodes.
- **Hotspot Clustering:** 8-connectivity connected-component labeling isolates individual violation zones, reporting centroid coordinates, bounding boxes, and affected silicon area.
- **Interactive Terminal Web Interface:** Pitch-black dark UI, terminal typography (`JetBrains Mono`), downsampled cursor probe, custom 1D cutlines, current headroom slider, and self-documenting provenance reports.
- **Automated CI/CD Integration:** Scriptable CLI with standardized exit codes (`0` on pass, `2` on violation, `1` on error) and exports in CSV, JSON, PNG, and printable HTML.

---

## Benchmark Layouts

The repository bundles calibrated synthetic testcases and taped-out silicon macros from the open-source IHP SG13G2 130nm BiCMOS PDK:

| Benchmark File | Category | Metal Stack | Footprint | Description / Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `samples/mesh_pdn.gds` | Synthetic | M1, M2, Via1 | 100 × 100 µm | Regular orthogonal power mesh with peripheral pad ring. |
| `samples/hierarchical_pdn.gds` | Synthetic | M1–M4, Via1–Via3, C4 | 150 × 150 µm | 4-layer power distribution tree with a 4×4 C4 bump grid. |
| `samples/bottleneck_pdn.gds` | Synthetic | M1, M2, Via1 | 100 × 100 µm | Defective PDN with induced via starvation and pinched stripes. |
| `samples/ihp/counter_top.gds` | IHP SG13G2 | 7-Metal Stack | 120 × 120 µm | Taped-out digital 8-bit counter macro with scan chain. |
| `samples/ihp/inverter_top.gds` | IHP SG13G2 | 7-Metal Stack | 65 × 45 µm | Analog/mixed-signal multi-stage inverter buffer chain. |
| `samples/ihp/sg13g2_ip__bondpad_70x70.gds` | IHP SG13G2 | TopMetal + Passiv. | 70 × 70 µm | Calibrated 70 µm wire-bond pad cell. |

---

## Installation

### Prerequisites

- Python 3.10, 3.11, 3.12, or 3.13
- Git

### Setup

```bash
git clone https://github.com/mouad11-11/GDSII-IR-DropAnalysisTool.git
cd GDSII-IR-DropAnalysisTool

# Create and activate virtual environment
python -m venv .venv

# Linux / macOS:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Install runtime dependencies
pip install -r requirements.txt

# Install development dependencies (optional, for running tests)
pip install -r requirements-dev.txt
```

---

## Quick Start

### Web Interface

Launch the local web server:

```bash
python run.py
```

Navigate to `http://127.0.0.1:8000`. The interface provides:
- Drag-and-drop GDSII upload with automatic file size sanitization.
- PDK technology selector (`default`, `ihp_sg13g2`) with interactive layer sheet resistance overrides.
- Multiple 2D visualization modes: IR-drop heatmap, margin slack map, absolute voltage, current flow vectors, and 3D surface view.
- Interactive cursor voltage probe and 1D cross-sectional slice generator.
- One-click export of CSV violation logs, JSON summaries, and printable HTML estimation certificates.

To launch headless without opening a browser window:

```bash
python run.py --no-browser --port 8080
```

---

### Command-Line Interface

Run automated headless evaluations:

```bash
# Baseline evaluation of standard power mesh
python cli.py samples/mesh_pdn.gds \
  --vnom 1.0 \
  --limit-mv 40.0 \
  --current 0.35 \
  --dist center_hotspot \
  --output report/

# Evaluation of IHP SG13G2 silicon macro using technology mapping
python cli.py samples/ihp/counter_top.gds \
  --tech ihp_sg13g2 \
  --net VDD \
  --vnom 1.2 \
  --limit-mv 50.0 \
  --current 0.05 \
  --output report_ihp/

# Fine discretization with AMG-CG solver
python cli.py samples/hierarchical_pdn.gds \
  --vnom 1.2 \
  --limit-mv 60.0 \
  --current 0.80 \
  --res 150 \
  --solver amg \
  --output report_hierarchical/
```

Generated artifacts in `--output`:
- `<name>_ir_drop_heatmap.png`: 2D spatial voltage drop heatmap with wire overlay.
- `<name>_margin_slack_heatmap.png`: Signed margin slack map (green = safe, red = violation).
- `<name>_3d_surface.png`: 3D potential surface perspective plot.
- `<name>_histogram.png`: Nodal voltage drop distribution with limit threshold line.
- `<name>_cutline_profile.png`: 1D orthogonal cross-sectional drop profiles.
- `<name>_violations.csv`: Tabular coordinates and voltage metrics for violating nodes (sorted worst-first).
- `<name>_report.json`: Machine-readable summary and metadata.

**CI/CD Exit Codes:**
- `0`: Analysis passed (all active nodes satisfy margin limit).
- `2`: Threshold violation detected ($\Delta V_{\text{max}} > \Delta V_{\text{limit}}$).
- `1`: Invalid input or execution error (e.g., floating sinks, missing pads).

---

### Python API

```python
from irdrop import run_analysis

layout, result, analysis, visualizer = run_analysis(
    gds_path="samples/mesh_pdn.gds",
    v_nom=1.0,
    delta_v_limit_mv=40.0,
    total_current=0.35,
    distribution="center_hotspot",
    grid_resolution=(120, 120),
    tech="default",
)

print(f"Status:          {analysis.status}")
print(f"Max IR Drop:     {analysis.delta_v_max_mv:.2f} mV")
print(f"Margin:          {analysis.margin_mv:+.2f} mV")
print(f"Safe Current:    {analysis.max_safe_current_ma:.1f} mA")
print(f"PDN Resistance:  {analysis.effective_pdn_resistance_ohm:.4f} Ohm")

fig = visualizer.generate_heatmap_figure(mode="ir_drop")
fig.savefig("mesh_ir_drop.png", bbox_inches="tight", dpi=150)
```

---

## CLI Reference

| Flag | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `gds_file` | `str` | *(required)* | Path to input GDSII layout file (`.gds` or `.gds2`). |
| `--tech` | `str` | `default` | Technology file name (`default`, `ihp_sg13g2`) or path to custom JSON. |
| `--net` | `str` | `VDD` | Target power net name to extract from labels. |
| `--vnom` | `float` | `1.0` | Nominal supply voltage $V_{\text{nom}}$ in Volts. |
| `--limit-mv` | `float` | `50.0` | IR drop tolerance threshold $\Delta V_{\text{limit}}$ in millivolts. |
| `--limit-pct` | `float` | `None` | Drop tolerance expressed as percentage of $V_{\text{nom}}$. |
| `--current` | `float` | `0.4` | Total supply current load $I_{\text{total}}$ in Amperes. |
| `--dist` | `choice` | `uniform` | Current load profile: `uniform`, `center_hotspot`, `dual_hotspot`, `quad_hotspot`. |
| `--res` | `int` | `100` | Grid resolution per axis ($N \times N$). |
| `--solver` | `choice` | `auto` | Linear solver method: `auto`, `cg`, `amg`, `direct`. |
| `--max-violations` | `int` | `1000` | Maximum number of violation rows in CSV (`0` for unlimited). |
| `--allow-default-pads` | `flag` | `False` | Fallback to peripheral pads if no pads or C4 bumps are detected. |
| `--guess-layers` | `flag` | `False` | Enable heuristic layer guessing for layers omitted from tech config. |
| `--output` | `str` | `report` | Directory where output plots, CSVs, and JSON files are saved. |
| `--no-overlay` | `flag` | `False` | Disable rendering layout wireframe polygons on top of heatmaps. |
| `--no-contours`| `flag` | `False` | Disable drawing isopotential contour lines. |

---

## Technology Configuration

PDK layer configurations are stored in `tech/<name>.json`. Example structure:

```json
{
  "name": "ihp_sg13g2",
  "description": "IHP SG13G2 130nm BiCMOS Open-Source PDK",
  "layers": [
    {
      "layer": 8,
      "datatype": 0,
      "name": "Metal 1",
      "role": "metal",
      "stack_order": 0,
      "sheet_resistance": 0.125
    },
    {
      "layer": 19,
      "datatype": 0,
      "name": "Via 1",
      "role": "via",
      "via_resistance": 1.5,
      "connects": [8, 10]
    }
  ]
}
```

---

## Test Suite

The test suite covers parser accuracy, tech stack classifications, width-aware conductances, AMG-CG solver convergence, and report generation:

```bash
pytest -v
```

Test modules:
- `test_irdrop.py`: Core PDN extraction, MNA solver, and visualization.
- `test_phase1.py`: Correctness assertions and exit code verification.
- `test_phase2.py`: Fail-fast boundary checks (missing pads, floating loads).
- `test_phase3.py`: PDK tech mapping and via cut counting.
- `test_phase4.py`: Width-aware conductance and analytic stripe drop.
- `test_phase5.py`: SPD linear system reduction and AMG-CG performance.
- `test_phase6.py`: Active-node analysis consistency and CSV sorting.
- `test_phase7.py`: Backend web hardening, input validation, and session cleanup.
- `test_phase8.py`: Analytic validation profiles and tool positioning checks.

---

## Repository Structure

```
.
├── cli.py                     # Headless command-line analysis interface
├── run.py                     # Web application server launcher
├── requirements.txt           # Production runtime package dependencies
├── requirements-dev.txt       # Development & test dependencies
├── pyproject.toml             # Project build and metadata configuration
├── pytest.ini                 # Pytest configuration
├── docs/                      # Technical documentation
│   └── VALIDATION.md          # Analytic benchmarks and OpenROAD PSM comparison
├── irdrop/                    # Core IR-drop analysis engine
│   ├── __init__.py            # Package API entrypoint
│   ├── gds_parser.py          # GDSII hierarchy extraction & polygon rasterization
│   ├── pdn_model.py           # 3D resistive network & boundary condition builder
│   ├── solver.py              # Sparse MNA solver & current distribution engine
│   ├── analyzer.py            # Margin evaluation metrics & hotspot clustering
│   ├── visualizer.py          # Matplotlib figure generators (2D, 3D, cutlines)
│   ├── tech.py                # Technology file and layer definition parser
│   └── sample_generator.py    # Synthetic PDN layout generator
├── tech/                      # Technology stack configurations
│   ├── default.json           # Default 4-metal tier reference stack
│   └── ihp_sg13g2.json        # IHP SG13G2 130nm 5-metal PDK stack
├── web/                       # Full-stack web dashboard
│   ├── app.py                 # FastAPI backend, REST endpoints & report exports
│   └── static/                # Pitch-black frontend interface (HTML, CSS, JS)
├── samples/                   # Benchmark layouts
│   ├── mesh_pdn.gds           # 2-layer orthogonal power mesh
│   ├── hierarchical_pdn.gds   # 4-layer hierarchical PDN with C4 bumps
│   ├── bottleneck_pdn.gds     # Power network with induced via starvation
│   └── ihp/                   # Open-source taped-out IHP SG13G2 silicon macros
└── tests/                     # Unit and integration test suite
    ├── test_irdrop.py         # Test cases for engine, solver, and visualizer
    ├── test_phase1.py         # Phase 1 correctness tests
    ├── test_phase2.py         # Phase 2 strict fail-fast validation tests
    ├── test_phase3.py         # Phase 3 tech file and layer mapping tests
    ├── test_phase4.py         # Phase 4 conductance and net selection tests
    ├── test_phase5.py         # Phase 5 solver and performance tests
    ├── test_phase6.py         # Phase 6 analysis consistency tests
    ├── test_phase7.py         # Phase 7 web hardening and provenance tests
    └── test_phase8.py         # Phase 8 validation and positioning tests
```

---

## License

This project is licensed under the [MIT License](LICENSE).
