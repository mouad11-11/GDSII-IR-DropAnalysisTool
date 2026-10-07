# Phase 4 Conductance & Area-Coverage Baseline Delta

## Overview
In Phase 4, binary occupancy is replaced with subpixel supersampled area-coverage fractions, and cell edge conductances are derived from true physical conductor widths ($R = R_{\text{sheet}} \cdot L / W$) using harmonic mean series formulation across adjacent half-cells.

Prior to Phase 4, the rasterizer dilated metal boundaries by 1 pixel and assumed every occupied cell was a full square ($W = \Delta y$), underestimating the resistance of narrow metal stripes by $2\times$ to $3\times$.

## Benchmark Deltas

| Benchmark | Parameter | Phase 0–3 Baseline | Phase 4 Geometry-Accurate | Delta | Physical Explanation |
|---|---|---|---|---|---|
| **mesh_pdn.gds** | Status | PASS | VIOLATION | Status Change | At 0.4A total current, real M1 standard cell rails (1.0 µm width) exhibit true resistance ($66.15\text{ mV}$ drop vs. dilated $35.08\text{ mV}$), exceeding the $50\text{ mV}$ limit. |
| | Max IR Drop | 35.08 mV | 66.15 mV | +31.07 mV | |
| | Precise Margin | +14.919 mV | -16.155 mV | -31.074 mV | |
| **hierarchical_pdn.gds** | Status | PASS | PASS | Maintained | 4-layer hierarchy with C4 flip-chip array maintains pass status, with drop shifting from $17.83\text{ mV}$ to $34.08\text{ mV}$ due to accurate M1/M2 stripe widths. |
| | Max IR Drop | 17.83 mV | 34.08 mV | +16.25 mV | |
| | Precise Margin | +32.171 mV | +15.917 mV | -16.254 mV | |
| **bottleneck_pdn.gds** | Status | VIOLATION | VIOLATION | Maintained | Defective necked lines and starved vias show intensified local drop ($144.74\text{ mV}$ vs dilated $68.23\text{ mV}$), strongly flagging the bottleneck. |
| | Max IR Drop | 68.23 mV | 144.74 mV | +76.51 mV | |
| | Precise Margin | -18.229 mV | -94.745 mV | -76.516 mV | |
