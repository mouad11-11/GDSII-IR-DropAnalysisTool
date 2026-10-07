# VoltDrop GDSII

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SciPy MNA](https://img.shields.io/badge/SciPy-Sparse_MNA-8CAAE6.svg?style=flat-square&logo=scipy&logoColor=white)](https://scipy.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey.svg?style=flat-square)]()
[![CI](https://github.com/mouad11-11/GDSII-IR-DropAnalysisTool/actions/workflows/ci.yml/badge.svg)](https://github.com/mouad11-11/GDSII-IR-DropAnalysisTool/actions/workflows/ci.yml)

Static IR-drop analysis and margin signoff verification engine for physical layouts.

VoltDrop parses standard GDSII stream files (`.gds`, `.gds2`), extracts multi-tier metal interconnects and inter-layer via arrays, models the physical power delivery network (PDN) as a 3D resistive conductance mesh, and solves the system using sparse Modified Nodal Analysis (MNA). It evaluates voltage drop margins against user-defined signoff thresholds, determines current budgets and safe headroom, clusters spatial hotspot violations, and delivers both an interactive dark web viewer and a scriptable CLI for automated signoff regressions.

---

## Table of Contents

- [Pipeline Architecture](#pipeline-architecture)
- [Mathematical Formulation](#mathematical-formulation)
- [Features](#features)
- [Benchmark Layouts](#benchmark-layouts)
- [Installation](#installation)
- [Quick Start](#quick-start)
  - [Web Interface](#web-interface)
  - [Command-Line Interface](#command-line-interface)
  - [Python API](#python-api)
- [CLI Reference](#cli-reference)
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
   | Polygon Extractor |       | Grid Discretization  |
   | (gdstk)           |       | (OpenCV)             |
   +-------------------+       +----------+-----------+
                                          |
                                          v
   +-------------------+       +----------------------+
   | Boundary & Source | ----> | 3D Conductance Mesh  |
   | Definitions       |       | (gx, gy, gvia)       |
   | (Pads / C4 / Sinks|       +----------+-----------+
   +-------------------+                  |
                                          v
                               +----------------------+
                               | Sparse MNA Matrix    |
                               | G * V = I            |
                               | (scipy.sparse)       |
                               +----------+-----------+
                                          |
                                          v
                               +----------------------+
                               | Margin Signoff &     |
                               | Hotspot Clustering   |
                               | (Slack, Headroom)    |
                               +----------+-----------+
                                          |
                        +-----------------+-----------------+
                        |                                   |
                        v                                   v
             +--------------------+              +--------------------+
             | Pitch-Black Web UI |              | CLI Batch Engine   |
             | Heatmaps, Cutlines |              | Plots, CSV, JSON   |
             | Signoff Report     |              | Signoff Verification
             +--------------------+              +--------------------+
```

---

## Mathematical Formulation

### 1. Admittance Matrix Assembly

VoltDrop discretizes layout metal layers into a uniform 2D grid per layer connected vertically by via contacts. The node potentials are governed by the Modified Nodal Analysis (MNA) linear system:

$$G \mathbf{v} = \mathbf{i}$$

Where:
- $G \in \mathbb{R}^{N \times N}$ is the sparse, symmetric positive-definite conductance matrix.
- $\mathbf{v} \in \mathbb{R}^N$ is the unknown electrical potential vector across all discretized grid nodes.
- $\mathbf{i} \in \mathbb{R}^N$ is the nodal current injection/sink vector.

### 2. Physical Conductance Equations

Intra-layer sheet resistance $R_\square$ defines horizontal and vertical conductances between neighboring grid cells $(x, y)$ of dimensions $\Delta x$ and $\Delta y$:

$$g_x = \frac{1}{R_\square} \cdot \frac{\Delta y}{\Delta x}, \quad g_y = \frac{1}{R_\square} \cdot \frac{\Delta x}{\Delta y}$$

Inter-layer conduction between overlapping metal polygons through via contacts with lumped resistance $R_{\text{via}}$ is modeled as:

$$g_{\text{via}} = \frac{1}{R_{\text{via}}}$$

### 3. Boundary Conditions & Current Injection

- **Dirichlet Boundary (Supply Pads / C4 Bumps):** Node potentials at designated pad locations $\mathcal{P}$ are pinned to the nominal supply voltage:
  $$V_p = V_{\text{nom}}, \quad \forall p \in \mathcal{P}$$
- **Neumann Boundary (Active Current Sinks):** Current loads are distributed across active standard cell rails $\mathcal{S}$ on bottom metal (M1), satisfying conservation of total current:
  $$\sum_{s \in \mathcal{S}} I_s = I_{\text{total}}$$
  Supported spatial load distributions include uniform, central hotspot, dual hotspot, and quad hotspot profiles.

### 4. Margin Signoff & Reliability Metrics

- **Nodal IR Drop:**
  $$\Delta V_k = V_{\text{nom}} - V_k$$
- **Absolute Voltage Margin:**
  $$\text{Margin } (\text{mV}) = \Delta V_{\text{limit}} - \Delta V_{\text{max}}$$
  A positive margin indicates signoff compliance (`PASS`), while a negative margin indicates a threshold violation (`VIOLATION`).
- **Margin Slack Ratio:**
  $$\text{Slack Ratio } (\%) = \frac{\Delta V_{\text{limit}} - \Delta V_{\text{max}}}{\Delta V_{\text{limit}}} \times 100\%$$
- **Maximum Safe Current Budget:**
  $$I_{\text{safe}} = I_{\text{total}} \cdot \left( \frac{\Delta V_{\text{limit}}}{\Delta V_{\text{max}}} \right)$$
- **Current Headroom:**
  $$\Delta I_{\text{headroom}} = I_{\text{safe}} - I_{\text{total}}$$
- **Effective PDN Resistance:**
  $$R_{\text{eff}} = \frac{\Delta V_{\text{avg}}}{I_{\text{total}}}, \quad R_{\text{peak}} = \frac{\Delta V_{\text{max}}}{I_{\text{total}}}$$

---

## Features

- **GDSII Stream Ingestion:** Native binary layout parsing through `gdstk` with boundary detection, cell reference flattening, and polygon rasterization via OpenCV.
- **Multi-Tier 3D PDN Modeling:** Configurable per-layer sheet resistances ($R_\square$), via contact resistances ($R_{\text{via}}$), and pad/bump geometries (peripheral rings, staggered pads, or C4 bump area-arrays).
- **Sparse Linear System Solver:** Memory-efficient Compressed Sparse Column (`csc_matrix`) admittance representation solved via direct sparse LU decomposition (`scipy.sparse.linalg.spsolve`).
- **Comprehensive Margin Signoff:** Rigorous metrics for maximum drop, minimum rail voltage, average drop, variance, slack percentage, current headroom, and effective impedance.
- **Hotspot Detection & Clustering:** Spatial grouping of violating nodes via 8-connectivity connected components to isolate critical power starvation regions, reporting total affected silicon area in $\mu\text{m}^2$ and percentage of active die.
- **Multimodal Visualization:**
  - 2D voltage drop ($\Delta V$) and absolute potential ($V$) distributions with optional layout wireframe overlay.
  - Margin slack map indicating local pass/fail headroom.
  - 1D cross-sectional cutline profiles along arbitrary X and Y axes.
  - 3D perspective potential surfaces and current flow vector fields ($\vec{J} \propto -\nabla V$).
  - Drop histogram with signoff threshold demarcation.
- **Dual Operating Modes:**
  - **Interactive Web Interface:** Pitch-black dark theme, terminal typography (`JetBrains Mono`), coordinate inspector, live parameter tuning, and printable signoff reports.
  - **Scriptable CLI:** Deterministic headless execution, exit codes for CI/CD integration, and automated generation of plots, CSV violation tables, and JSON manifests.

---

## Benchmark Layouts

The repository includes synthetic verification testcases and real taped-out silicon macros from the open-source IHP SG13G2 130nm BiCMOS PDK:

| Benchmark File | Category | Metal Stack | Die Footprint | Description / Purpose |
|---|---|---|---|---|
| `samples/mesh_pdn.gds` | Synthetic | M1, M2, Via1 | 100 µm × 100 µm | Regular orthogonal power mesh with peripheral pad ring for baseline solver validation. |
| `samples/hierarchical_pdn.gds` | Synthetic | M1–M4, Via1–Via3, C4 | 150 µm × 150 µm | 4-layer power distribution tree with a 4×4 C4 area-array bump grid. |
| `samples/bottleneck_pdn.gds` | Synthetic | M1, M2, Via1 | 100 µm × 100 µm | Mesh containing localized via starvation and necked metal lines for margin violation testing. |
| `samples/ihp/counter_top.gds` | Silicon PDK | TopLevel Standard Cell | Taped-out Macro | Synchronous 8-bit counter macro synthesized on IHP SG13G2 130nm process. |
| `samples/ihp/inverter_top.gds` | Silicon PDK | TopLevel Standard Cell | Taped-out Macro | High-speed multi-stage inverter buffer chain from IHP SG13G2 PDK. |
| `samples/ihp/sg13g2_ip__bondpad_70x70.gds` | Silicon PDK | TopMetal + Passivation | 70 µm × 70 µm | Calibrated 70 µm wire-bond pad cell for top-metal resistance extraction. |
| `samples/ihp/chip_top.gds` | Silicon PDK | Full Chip Hierarchy | Multi-mm² Die | Complete SoC top-level layout for large-scale hierarchical hierarchy parsing. |

---

## Installation

### Prerequisites

- Python 3.10 or higher
- Git

### Setup

Clone the repository and install dependencies in an isolated virtual environment:

```bash
git clone https://github.com/mouad11-11/GDSII-IR-DropAnalysisTool.git
cd GDSII-IR-DropAnalysisTool

# Create virtual environment
python -m venv .venv

# Activate environment
# On Linux / macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

---

## Quick Start

### Web Interface

Launch the interactive local server:

```bash
python run.py
```

Open `http://127.0.0.1:8000` in your browser. The web dashboard provides:
- File upload for custom GDSII layouts or one-click loading of bundled benchmark files.
- Real-time configuration of nominal voltage ($V_{\text{nom}}$), drop tolerance limit ($\Delta V_{\text{limit}}$), total current ($I_{\text{total}}$), and spatial load distributions.
- Pitch-black layout canvas with pan, zoom, layer visibility toggles, and coordinate inspection.
- Direct export of high-resolution PNG plots, CSV violation spreadsheets, structured JSON data, and printable HTML signoff reports.

To launch without automatically opening a browser window:

```bash
python run.py --no-browser --port 8080
```

---

### Command-Line Interface

VoltDrop provides a headless CLI for automated batch processing, regressions, and continuous integration:

```bash
# Baseline evaluation of standard power mesh
python cli.py samples/mesh_pdn.gds \
  --vnom 1.0 \
  --limit-mv 40.0 \
  --current 0.35 \
  --dist center_hotspot \
  --output report/

# Margin violation signoff on bottleneck design
python cli.py samples/bottleneck_pdn.gds \
  --vnom 1.0 \
  --limit-mv 45.0 \
  --current 0.40 \
  --output report/

# Hierarchical 4-layer PDN with fine discretization
python cli.py samples/hierarchical_pdn.gds \
  --vnom 1.2 \
  --limit-mv 60.0 \
  --current 0.80 \
  --res 150 \
  --output report_hierarchical/
```

Generated output artifacts in `--output`:
- `<name>_ir_drop_heatmap.png`: 2D spatial voltage drop heatmap with wire overlay and contours.
- `<name>_margin_slack_heatmap.png`: Signed margin slack map (green = safe, red = violation).
- `<name>_3d_surface.png`: 3D potential surface perspective plot.
- `<name>_histogram.png`: Nodal voltage drop distribution with limit threshold line.
- `<name>_cutline_profile.png`: 1D orthogonal cross-sectional drop profiles.
- `<name>_violations.csv`: Tabular coordinates and voltage metrics for violating nodes.
- `<name>_report.json`: Machine-readable signoff summary and metadata.

Exit codes for CI/CD automation: `0` on signoff `PASS`, `2` on threshold `VIOLATION`, and `1` on invalid input or runtime error.

---

### Python API

VoltDrop can be embedded directly into custom physical design and analysis pipelines:

```python
from irdrop import run_analysis

# Execute end-to-end extraction and solving
layout, result, analysis, visualizer = run_analysis(
    gds_path="samples/mesh_pdn.gds",
    v_nom=1.0,
    delta_v_limit_mv=40.0,
    total_current=0.35,
    distribution="center_hotspot",
    grid_resolution=(120, 120),
)

print(f"Status:             {analysis.status}")
print(f"Max IR Drop:        {analysis.delta_v_max_mv:.2f} mV")
print(f"Signoff Margin:     {analysis.margin_mv:+.2f} mV")
print(f"Safe Current:       {analysis.max_safe_current_ma:.1f} mA")
print(f"Effective PDN R:    {analysis.effective_pdn_resistance_ohm:.4f} Ohm")

# Generate figures programmatically
fig = visualizer.generate_heatmap_figure(mode="ir_drop")
fig.savefig("mesh_ir_drop.png", bbox_inches="tight", dpi=150)
```

---

## CLI Reference

| Flag | Type | Default | Description |
|---|---|---|---|
| `gds_file` | `str` | *(required)* | Path to input GDSII layout file (`.gds` or `.gds2`). |
| `--vnom` | `float` | `1.0` | Nominal supply voltage $V_{\text{nom}}$ in Volts. |
| `--limit-mv` | `float` | `50.0` | IR drop tolerance threshold $\Delta V_{\text{limit}}$ in millivolts. |
| `--limit-pct` | `float` | `None` | Drop tolerance expressed as percentage of $V_{\text{nom}}$ (e.g., `5.0` for 5%). |
| `--current` | `float` | `0.4` | Total supply current load $I_{\text{total}}$ in Amperes. |
| `--dist` | `choice` | `uniform` | Current load profile: `uniform`, `center_hotspot`, `dual_hotspot`, `quad_hotspot`. |
| `--net` | `str` | `VDD` | Target power net to analyze (`VDD`, `VSS`, etc.). |
| `--res` | `int` | `100` | Discretization grid resolution per axis ($N \times N$). |
| `--output` | `str` | `report` | Directory where plots, CSV tables, and JSON manifests are saved. |
| `--no-overlay` | `flag` | `False` | Disable rendering layout wireframe polygons on top of heatmaps. |
| `--no-contours`| `flag` | `False` | Disable drawing isopotential contour lines. |
| `--tech` | `str` | `default` | Technology file name or JSON path (e.g., `default`, `ihp_sg13g2`). |
| `--guess-layers` | `flag` | `False` | Enable heuristic layer guessing for layers omitted from technology configuration (warns when used). |
| `--allow-default-pads` | `flag` | `False` | Allow default boundary pad fallback if no power pads or C4 bumps are detected. |
| `--solver` | `choice` | `auto` | Linear system solver method: `auto`, `cg`, `amg`, `direct`. |
| `--max-violations` | `int` | `1000` | Maximum number of violation nodes to export in CSV (0 for unlimited). |

---

## Test Suite

The test suite validates GDSII parsing, multi-layer PDN mesh generation, sparse MNA matrix formulation, margin assertions (both pass and violation conditions), and figure generation.

Run all tests:

```bash
pytest -v
```

Test coverage includes:
- `test_gds_parser`: Validates polygon extraction, layer segregation, and bounding box computation.
- `test_pdn_builder_and_solver`: Asserts grid mapping, boundary conditions, and sparse linear system convergence.
- `test_precise_value_margin_pass`: Verifies margin calculations when maximum drop is strictly within tolerance limits.
- `test_precise_value_margin_violation`: Confirms correct violation detection, hotspot clustering, and negative slack accounting.
- `test_visualizer_figures`: Verifies generation of 2D heatmaps, 3D surface meshes, cutlines, and histograms.

---

## Repository Structure

```
.
├── cli.py                     # Headless command-line analysis interface
├── run.py                     # Web application server launcher
├── requirements.txt           # Python package dependencies
├── pyproject.toml             # Project build and tool configurations
├── pytest.ini                 # Pytest discovery and pythonpath configuration
├── irdrop/                    # Core IR-drop analysis engine
│   ├── __init__.py            # Package API entrypoint
│   ├── gds_parser.py          # GDSII hierarchy extraction & polygon rasterization
│   ├── pdn_model.py           # 3D resistive network & boundary condition builder
│   ├── solver.py              # Sparse MNA solver & current distribution engine
│   ├── analyzer.py            # Margin signoff metrics & connected component hotspot clustering
│   ├── visualizer.py          # Matplotlib figures (2D heatmaps, 3D surfaces, cutlines)
│   ├── tech.py                # Technology file and layer definition parser
│   └── sample_generator.py    # Synthetic PDN layout generator
├── tech/                      # Technology stack configurations
│   ├── default.json           # Default 4-metal tier reference stack
│   └── ihp_sg13g2.json        # IHP SG13G2 130nm 5-metal PDK stack
├── web/                       # Full-stack web dashboard
│   ├── app.py                 # FastAPI backend, REST endpoints & report generation
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
    └── test_phase6.py         # Phase 6 analysis consistency tests
```

---

## License

This project is licensed under the [MIT License](LICENSE).
