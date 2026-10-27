import xml.etree.ElementTree as ET, json
from pathlib import Path

root   = Path.home() / "Smart-Transport-SUMO" / "mc_results"
target = {"-2535039#2", "79633771#0", "79633771#2"}
focus  = [21600, 25200, 28800, 32400, 57600, 61200, 64800, 68400]

records = []
for ckpt_s in focus:
    base_pred = root / f"ckpt_{ckpt_s:05d}"    / "mc_prediction.edg.xml"
    bn_pred   = root / f"ckpt_bn_{ckpt_s:05d}" / "mc_prediction.edg.xml"

    def all_densities(path, eid):
        if not path.exists():
            return []
        tree = ET.parse(path)
        return [
            {"begin": float(iv.get("begin")), "end": float(iv.get("end")),
             "density": float(e.get("density", 0)), "speed": float(e.get("speed", 0)),
             "density_min": float(e.get("density_min", 0)),
             "density_max": float(e.get("density_max", 0)),
             "speed_std": float(e.get("speed_std", 0))}
            for iv in tree.getroot().findall("interval")
            for e in iv.findall("edge") if e.get("id") == eid
        ]

    for eid in sorted(target):
        records.append({
            "checkpoint_s": ckpt_s,
            "checkpoint_hhmm": f"{ckpt_s//3600:02d}:00",
            "edge": eid,
            "baseline":   all_densities(base_pred, eid),
            "bottleneck": all_densities(bn_pred,   eid),
        })

print(json.dumps(records, indent=2))