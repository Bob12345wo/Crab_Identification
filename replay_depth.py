"""Recalculate thickness from saved aligned depth without an OAK device."""
import argparse, json
from pathlib import Path
import numpy as np
from crab_thickness import measure_thickness

def main():
    p=argparse.ArgumentParser(); p.add_argument('--depth',required=True); p.add_argument('--measurement',required=True); p.add_argument('--output'); p.add_argument('--body-scale',type=float,default=.35); p.add_argument('--max-height-mm',type=float,default=150.); p.add_argument('--plane-tolerance-mm',type=float,default=3.); a=p.parse_args()
    with np.load(a.depth,allow_pickle=False) as sample:
        for key in ('depth_mm','intrinsics'):
            if key not in sample: raise SystemExit(f'NPZ must contain {key}')
        depth,intrinsics=sample['depth_mm'],sample['intrinsics']
    measurement=json.loads(Path(a.measurement).read_text(encoding='utf-8')); bbox=(measurement.get('pose') or {}).get('bbox_xyxy')
    if not bbox: raise SystemExit('Measurement JSON does not contain pose.bbox_xyxy')
    result=measure_thickness(depth,intrinsics,bbox,a.body_scale,a.max_height_mm,a.plane_tolerance_mm)
    result.update(source_depth=str(Path(a.depth).resolve()),source_measurement=str(Path(a.measurement).resolve()),source_measurement_id=measurement.get('measurement_id'))
    text=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output: Path(a.output).write_text(text,encoding='utf-8')
    print(text); raise SystemExit(0 if result.get('ok') else 2)
if __name__=='__main__': main()
