# VoltDrop GDSII: Physical IR-Drop Analysis & Precise Value Margin Signoff Tool

An Integrated Circuit (IC) Power Delivery Network (PDN) analysis platform that imports raw **GDSII layout files** (`.gds`, `.gds2`), extracts the physical multi-tier metal mesh and via connectivity, solves the static conductance network using sparse Modified Nodal Analysis (MNA), and computes **Precise Value Margins** alongside high-resolution interactive **heatmaps**, **voltage topography**, and **1D cross-section cutlines**.

---

## 🌟 Key Features

1. **GDSII Layout Parser & Rasterizer**:
   - Built on `gdstk` for C++ speed and precision.
   - Flattens cell hierarchies, extracts polygons, paths, and text labels (e.g. `VDD_PAD`, `C4_VDD`).
   - Auto-classifies metal layers (M1, M2, M3, M4...), via layers (V1, V2, V3...), and power pads / C4 bump pins.
   - Ultra-fast polygon rasterization via OpenCV.

2. **Physical PDN Conductance Modeling**:
   - Constructs multi-tier 3D resistive grids coupling standard cell power rails, intermediate distribution straps, and top global power meshes.
   - Accurate intra-layer sheet resistances ($R_\square \text{ in }\Omega/\text{sq}$) and inter-layer via contact resistances ($R_{via} \text{ in }\Omega$).
   - Dirichlet boundary conditions for power pads / C4 bumps.
   - Configurable current sinks (Standard cells on M1) with multiple spatial activity models:
     - Uniform active die distribution
     - Central high-power processing core hotspot
     - Dual-core compute clusters
     - Quad-core SoC activity profiles

3. **Precise Value Margin Signoff Engine**:
   - **Margin Slack Metric**:
     $$\text{Margin } (\text{mV}) = \Delta V_{\text{limit}} - \Delta V_{\text{max}} = V_{\text{min, observed}} - V_{\text{min, allowed}}$$
   - **Slack Ratio**:
     $$\text{Margin \%} = \frac{\Delta V_{\text{limit}} - \Delta V_{\text{max}}}{\Delta V_{\text{limit}}} \times 100\%$$
   - **Signoff Decision**:
     - $\text{Margin} \ge 0 \implies$ **`PASS (SAFE)`**
     - $\text{Margin} < 0 \implies$ **`VIOLATION (FAIL)`**
   - Pinpoints worst-case $(X, Y)$ coordinates, layer index, exact millivolt drop, and min observed voltage down to microvolts.
   - Automated hotspot clustering with bounding boxes, violation areas, and severity ratings.
   - Statistical distribution metrics: Mean, Std Dev, $P_{90}$, $P_{95}$, $P_{99}$.

4. **Multi-Mode Visualization**:
   - **Static IR-Drop Heatmap** ($\Delta V$ in mV) with isopotential contour lines.
   - **PDN Potential Landscape** ($V(x,y)$ in Volts).
   - **Margin Slack Map** (Local headroom/slack in mV: Green = safe headroom, Red = margin violation).
   - **Physical GDSII Wire Overlay**: Displays layout wire geometry directly on top of the heatmaps.
   - **Interactive Cursor Probe HUD**: Hover anywhere on the chip to probe exact coordinates $(X,Y)$, drop, voltage, and margin.
   - **Dynamic 1D Cutline Inspector**: Interactive slider to inspect voltage drop profiles across any horizontal cross-section.

5. **Two Execution Modes**:
   - **Interactive Web Application**: Modern dark-theme GUI with real-time feedback, sample loaders, and report download.
   - **Headless CLI Tool**: Scriptable for automated CI/CD and EDA design flow regression.

---

## 🚀 Quick Start

### 1. Launch the Web Application
```bash
python run.py
```
This automatically starts the FastAPI server and opens your web browser at `http://127.0.0.1:8000`.

### 2. Run via Command-Line Interface (CLI)
```bash
# Basic run on sample
python cli.py samples/mesh_pdn.gds --vnom 1.0 --limit-mv 40.0 --current 0.35 --dist center_hotspot --output report/

# Run on a defect/bottleneck sample to verify violation detection
python cli.py samples/bottleneck_pdn.gds --vnom 1.0 --limit-mv 45.0 --current 0.40 --output report/
```

### 3. Run the Test Suite
```bash
python -m pytest tests/test_irdrop.py -v
```

---

## 📁 Pre-Generated Benchmark Samples

The repository includes three realistic benchmark layouts in `samples/`:
1. `samples/mesh_pdn.gds`:
   - 2-layer power mesh (horizontal M1 stdcell rails, vertical M2 stripes, via contacts, and peripheral VDD pads).
2. `samples/hierarchical_pdn.gds`:
   - 4-metal layer hierarchical SoC PDN (M1, M2, M3, M4) with a 4x4 array of C4 flip-chip power bumps.
3. `samples/bottleneck_pdn.gds`:
   - PDN with missing Northeast vias and pinched M2 stripes to demonstrate margin violation detection, negative margin calculation, and hotspot isolation.

---

## 🧮 Mathematical Formulation

The power delivery network is represented as an admittance circuit matrix:

$$G \cdot V = I$$

Where:
- $G \in \mathbb{R}^{N \times N}$ is the sparse conductance matrix.
- For nodes $i$ and $j$ connected by wire resistance $R_{ij}$, $G_{ii} = \sum_j \frac{1}{R_{ij}}$, $G_{ij} = -\frac{1}{R_{ij}}$.
- For power pad nodes $p \in \mathcal{P}$, Dirichlet boundary conditions fix $V_p = V_{\text{nom}}$.
- For logic sink nodes $s \in \mathcal{S}$ on M1, current $I_s$ is drawn such that $\sum_{s} I_s = I_{\text{total}}$.
- Static IR drop is:
  $$\Delta V_k = V_{\text{nom}} - V_k$$

---

## 📂 Repository Structure

```
IR-dropAnalysis/
├── irdrop/
│   ├── __init__.py           # Package exports & high-level run_analysis API
│   ├── gds_parser.py         # GDSII parsing with gdstk and OpenCV rasterizer
│   ├── pdn_model.py          # Multi-tier 3D resistive grid builder
│   ├── solver.py             # Sparse MNA solver (SciPy SuperLU)
│   ├── analyzer.py           # Precise Value Margin & hotspot engine
│   ├── visualizer.py         # Publication-quality heatmaps, cutlines, overlays
│   └── sample_generator.py   # Benchmark GDSII generators
├── web/
│   ├── app.py                # FastAPI backend endpoints
│   └── static/
│       ├── index.html        # Single-page interactive user interface
│       ├── app.js            # Frontend controller & canvas hover probe
│       └── style.css         # Modern dark VLSI engineering styling
├── samples/                  # Pre-compiled benchmark GDSII layouts
│   ├── mesh_pdn.gds
│   ├── hierarchical_pdn.gds
│   └── bottleneck_pdn.gds
├── tests/
│   └── test_irdrop.py        # Pytest test suite
├── cli.py                    # Scriptable CLI tool
├── run.py                    # Browser auto-launch server script
└── README.md
```

---

## 📄 License
MIT License.
