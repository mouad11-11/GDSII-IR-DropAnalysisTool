"""Technology file loader and layer mapping configuration."""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TechLayer:
    layer: int
    datatype: int = 0
    name: str = ""
    role: str = "metal"  # 'metal', 'via', 'pad', 'ignore', 'pin', 'label', 'boundary'
    stack_order: Optional[int] = None
    sheet_resistance: float = 0.05
    via_resistance: float = 1.5
    connects: Optional[List[int]] = None


@dataclass
class TechConfig:
    name: str
    description: str
    layers: Dict[Tuple[int, int], TechLayer] = field(default_factory=dict)

    def get_layer(self, layer: int, datatype: int = 0) -> Optional[TechLayer]:
        return self.layers.get((layer, datatype))

    def get_metal_layers(self) -> List[TechLayer]:
        metals = [l for l in self.layers.values() if l.role == "metal"]
        metals.sort(key=lambda m: (m.stack_order if m.stack_order is not None else 999, m.layer))
        return metals

    def get_via_layers(self) -> List[TechLayer]:
        return [l for l in self.layers.values() if l.role == "via"]

    def get_pad_layers(self) -> List[TechLayer]:
        return [l for l in self.layers.values() if l.role == "pad"]

    def get_via_between(self, bottom_layer: int, top_layer: int) -> Optional[TechLayer]:
        """Finds via layer explicitly connecting bottom_layer and top_layer."""
        for l in self.layers.values():
            if l.role == "via" and l.connects:
                if (l.connects[0] == bottom_layer and l.connects[1] == top_layer) or \
                   (l.connects[0] == top_layer and l.connects[1] == bottom_layer):
                    return l
        return None


def get_tech_dir() -> Path:
    """Returns the path to the tech directory."""
    pkg_root = Path(__file__).parent.parent
    tech_dir = pkg_root / "tech"
    if tech_dir.exists():
        return tech_dir
    return Path("tech")


def load_tech_file(name_or_path: str = "default") -> TechConfig:
    """
    Loads technology mapping configuration from a name (e.g. 'default', 'ihp_sg13g2')
    or from a direct filesystem path to a JSON file.
    """
    path = Path(name_or_path)
    if not path.is_file():
        # Check in tech directory
        tech_dir = get_tech_dir()
        candidate = tech_dir / f"{name_or_path}.json"
        if candidate.is_file():
            path = candidate
        else:
            candidate_raw = tech_dir / name_or_path
            if candidate_raw.is_file():
                path = candidate_raw
            else:
                raise FileNotFoundError(f"Tech file not found: {name_or_path} (checked {candidate})")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    name = data.get("name", path.stem)
    description = data.get("description", "")
    layers_dict: Dict[Tuple[int, int], TechLayer] = {}

    raw_layers = data.get("layers", [])
    if isinstance(raw_layers, list):
        for item in raw_layers:
            layer = int(item["layer"])
            datatype = int(item.get("datatype", 0))
            t_layer = TechLayer(
                layer=layer,
                datatype=datatype,
                name=item.get("name", f"L{layer}D{datatype}"),
                role=item.get("role", "metal"),
                stack_order=item.get("stack_order"),
                sheet_resistance=float(item.get("sheet_resistance", 0.05)),
                via_resistance=float(item.get("via_resistance", 1.5)),
                connects=item.get("connects"),
            )
            layers_dict[(layer, datatype)] = t_layer
    elif isinstance(raw_layers, dict):
        for key, item in raw_layers.items():
            parts = key.split(":")
            layer = int(parts[0])
            datatype = int(parts[1]) if len(parts) > 1 else 0
            t_layer = TechLayer(
                layer=layer,
                datatype=datatype,
                name=item.get("name", f"L{layer}D{datatype}"),
                role=item.get("role", "metal"),
                stack_order=item.get("stack_order"),
                sheet_resistance=float(item.get("sheet_resistance", 0.05)),
                via_resistance=float(item.get("via_resistance", 1.5)),
                connects=item.get("connects"),
            )
            layers_dict[(layer, datatype)] = t_layer

    return TechConfig(name=name, description=description, layers=layers_dict)
