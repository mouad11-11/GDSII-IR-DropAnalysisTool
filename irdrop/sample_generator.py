"""
Sample GDSII Generator for Power Delivery Network (PDN) Benchmarks.
Generates realistic multi-tier IC power grids with metal rails, stripes, vias, and power pads.
"""

from pathlib import Path
import numpy as np
import gdstk

def create_mesh_pdn(
    output_path: str = "samples/mesh_pdn.gds",
    die_size: tuple[float, float] = (200.0, 200.0),
    m1_pitch: float = 8.0,
    m1_width: float = 1.0,
    m2_pitch: float = 25.0,
    m2_width: float = 3.0,
    via_size: float = 1.0,
    pad_size: float = 15.0,
) -> str:
    """
    Creates a 2-layer power mesh GDSII.
    Layer 1: M1 horizontal rails (VDD)
    Layer 2: Via1 contacts
    Layer 3: M2 vertical stripes (VDD)
    Layer 10: VDD Power Pads (periphery)
    """
    lib = gdstk.Library("MESH_PDN_LIB", unit=1e-6, precision=1e-9)
    cell = lib.new_cell("MESH_PDN_TOP")

    width, height = die_size

    # Layer 1: M1 Horizontal Rails
    y_coords = np.arange(m1_pitch / 2, height, m1_pitch)
    for y in y_coords:
        rect = gdstk.rectangle((0, y - m1_width / 2), (width, y + m1_width / 2), layer=1, datatype=0)
        cell.add(rect)

    # Layer 3: M2 Vertical Stripes
    x_coords = np.arange(m2_pitch / 2, width, m2_pitch)
    for x in x_coords:
        rect = gdstk.rectangle((x - m2_width / 2, 0), (x + m2_width / 2, height), layer=3, datatype=0)
        cell.add(rect)

    # Layer 2: Via1 at intersections of M1 and M2
    for x in x_coords:
        for y in y_coords:
            v_rect = gdstk.rectangle(
                (x - via_size / 2, y - via_size / 2),
                (x + via_size / 2, y + via_size / 2),
                layer=2,
                datatype=0,
            )
            cell.add(v_rect)

    # Layer 10: VDD Power Pads at 4 corners and mid edges
    pad_positions = [
        (pad_size / 2, pad_size / 2),
        (width - pad_size / 2, pad_size / 2),
        (pad_size / 2, height - pad_size / 2),
        (width - pad_size / 2, height - pad_size / 2),
        (width / 2, pad_size / 2),
        (width / 2, height - pad_size / 2),
    ]

    for px, py in pad_positions:
        pad = gdstk.rectangle(
            (px - pad_size / 2, py - pad_size / 2),
            (px + pad_size / 2, py + pad_size / 2),
            layer=10,
            datatype=0,
        )
        cell.add(pad)
        # Add text label
        label = gdstk.Label("VDD_PAD", (px, py), layer=10, texttype=0)
        cell.add(label)

    # Add die outline on Layer 63
    die_box = gdstk.rectangle((0, 0), (width, height), layer=63, datatype=0)
    cell.add(die_box)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    lib.write_gds(output_path)
    return str(Path(output_path).resolve())


def create_hierarchical_pdn(
    output_path: str = "samples/hierarchical_pdn.gds",
    die_size: tuple[float, float] = (400.0, 400.0),
) -> str:
    """
    Creates a 4-metal layer hierarchical SoC PDN.
    Layer 1: M1 horizontal stdcell rails
    Layer 2: Via1
    Layer 3: M2 vertical stripes
    Layer 4: Via2
    Layer 5: M3 horizontal power straps
    Layer 6: Via3
    Layer 7: M4 top global thick metal mesh
    Layer 10: C4 Bumps (flip-chip array)
    """
    lib = gdstk.Library("HIERARCHICAL_PDN_LIB", unit=1e-6, precision=1e-9)
    top_cell = lib.new_cell("SOC_PDN_TOP")

    width, height = die_size

    # Layer 1: M1 Rails (pitch 6 um, width 0.8 um)
    y_m1 = np.arange(3.0, height, 6.0)
    for y in y_m1:
        top_cell.add(gdstk.rectangle((0, y - 0.4), (width, y + 0.4), layer=1, datatype=0))

    # Layer 3: M2 Stripes (pitch 20 um, width 1.8 um)
    x_m2 = np.arange(10.0, width, 20.0)
    for x in x_m2:
        top_cell.add(gdstk.rectangle((x - 0.9, 0), (x + 0.9, height), layer=3, datatype=0))

    # Layer 2: Via1 (M1 to M2)
    for x in x_m2:
        for y in y_m1:
            top_cell.add(gdstk.rectangle((x - 0.4, y - 0.4), (x + 0.4, y + 0.4), layer=2, datatype=0))

    # Layer 5: M3 Straps (pitch 40 um, width 3.0 um)
    y_m3 = np.arange(20.0, height, 40.0)
    for y in y_m3:
        top_cell.add(gdstk.rectangle((0, y - 1.5), (width, y + 1.5), layer=5, datatype=0))

    # Layer 4: Via2 (M2 to M3)
    for x in x_m2:
        for y in y_m3:
            top_cell.add(gdstk.rectangle((x - 0.7, y - 0.7), (x + 0.7, y + 0.7), layer=4, datatype=0))

    # Layer 7: M4 Top Global Mesh (pitch 80 um, width 6.0 um)
    x_m4 = np.arange(40.0, width, 80.0)
    for x in x_m4:
        top_cell.add(gdstk.rectangle((x - 3.0, 0), (x + 3.0, height), layer=7, datatype=0))

    # Layer 6: Via3 (M3 to M4)
    for x in x_m4:
        for y in y_m3:
            top_cell.add(gdstk.rectangle((x - 1.2, y - 1.2), (x + 1.2, y + 1.2), layer=6, datatype=0))

    # Layer 10: C4 Bumps array (4x4 array of 20um octagons/rectangles)
    bump_xs = np.linspace(50.0, width - 50.0, 4)
    bump_ys = np.linspace(50.0, height - 50.0, 4)
    for bx in bump_xs:
        for by in bump_ys:
            bump = gdstk.regular_polygon((bx, by), 12.0, 8, layer=10, datatype=0)
            top_cell.add(bump)
            label = gdstk.Label("C4_VDD", (bx, by), layer=10)
            top_cell.add(label)

    # Die boundary
    top_cell.add(gdstk.rectangle((0, 0), (width, height), layer=63, datatype=0))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    lib.write_gds(output_path)
    return str(Path(output_path).resolve())


def create_bottleneck_pdn(
    output_path: str = "samples/bottleneck_pdn.gds",
    die_size: tuple[float, float] = (250.0, 250.0),
) -> str:
    """
    Creates a PDN with an intentional defect/bottleneck:
    Missing vias in the northeast quadrant and severed M2 stripes,
    causing severe IR drop hotspots and negative margin (violation).
    """
    lib = gdstk.Library("BOTTLENECK_PDN_LIB", unit=1e-6, precision=1e-9)
    cell = lib.new_cell("BOTTLENECK_PDN_TOP")

    width, height = die_size

    # Layer 1: M1 Rails
    y_coords = np.arange(8.0, height, 10.0)
    for y in y_coords:
        cell.add(gdstk.rectangle((0, y - 0.5), (width, y + 0.5), layer=1, datatype=0))

    # Layer 3: M2 Vertical Stripes (with gap/necking in quadrant 2)
    x_coords = np.arange(15.0, width, 30.0)
    for x in x_coords:
        if 130 <= x <= 220:
            # Defective gap in northern half
            cell.add(gdstk.rectangle((x - 1.0, 0), (x + 1.0, 110), layer=3, datatype=0))
            # Narrow necking in upper region
            cell.add(gdstk.rectangle((x - 0.2, 110), (x + 0.2, height), layer=3, datatype=0))
        else:
            cell.add(gdstk.rectangle((x - 1.5, 0), (x + 1.5, height), layer=3, datatype=0))

    # Layer 2: Vias (intentionally omit vias in Northeast region: x > 120 and y > 120)
    for x in x_coords:
        for y in y_coords:
            if x > 120 and y > 120:
                continue  # MISSING VIAS DEFECT!
            cell.add(gdstk.rectangle((x - 0.6, y - 0.6), (x + 0.6, y + 0.6), layer=2, datatype=0))

    # Layer 10: VDD Pads only on bottom edge and West edge (asymmetric feed)
    pad_positions = [
        (25.0, 20.0),
        (100.0, 20.0),
        (20.0, 100.0),
        (20.0, 200.0),
    ]
    for px, py in pad_positions:
        cell.add(gdstk.rectangle((px - 10, py - 10), (px + 10, py + 10), layer=10, datatype=0))
        cell.add(gdstk.Label("VDD_PAD", (px, py), layer=10))

    cell.add(gdstk.rectangle((0, 0), (width, height), layer=63, datatype=0))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    lib.write_gds(output_path)
    return str(Path(output_path).resolve())


def generate_all_samples():
    """Generates all benchmark samples."""
    print("Generating mesh_pdn.gds...")
    create_mesh_pdn()
    print("Generating hierarchical_pdn.gds...")
    create_hierarchical_pdn()
    print("Generating bottleneck_pdn.gds...")
    create_bottleneck_pdn()
    print("All sample GDSII files successfully created in samples/")

if __name__ == "__main__":
    generate_all_samples()
