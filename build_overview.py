#!/usr/bin/env python3
"""Aggregate Event-Core data across all SEACAR DwC-A datasets into JSON bundles
for the overview HTML. Aggregation uses H3 hexagons (res 7 fine + res 5 coarse)."""

import json
import re
from pathlib import Path

import h3
import pandas as pd

DWC_DIR = Path("../seacar2dwc/data/dwc")
OUT_DIR = Path("output")

RES_FINE = 7    # ~5.2 km² hexes
RES_COARSE = 5  # ~177 km² hexes
YEAR_MIN, YEAR_MAX = 1900, 2030

datasets = []
hex_fine = {}    # (ds_idx, h3 res7) -> count
hex_coarse = {}  # (ds_idx, h3 res5) -> count
ts = {}          # (ds_idx, year) -> count


def main():
    for d in sorted(DWC_DIR.iterdir()):
        if not d.is_dir():
            continue
        dsid = d.name
        event_path = d / "archive" / "event.txt"
        occ_path = d / "archive" / "occurrence.txt"

        title, bbox_txt = "", ""
        summary = d / "archive_summary.md"
        if summary.exists():
            text = summary.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^## Metadata.*?^- Title: ([^\n]+)", text, re.S | re.M)
            if m:
                title = m.group(1).strip()
            mb = re.search(r"^- Bounding box: (.+)$", text, re.M)
            if mb:
                bbox_txt = mb.group(1).strip()

        n_occ = 0
        if occ_path.exists():
            with open(occ_path, "rb") as fh:
                n_occ = sum(1 for _ in fh) - 1

        di = len(datasets)
        n_events = 0
        years = set()
        lat_min = lat_max = lon_min = lon_max = None
        n_nogeom = 0

        if event_path.exists():
            rows = 0
            for chunk in pd.read_csv(
                event_path, sep="\t", usecols=["decimalLatitude", "decimalLongitude", "year"],
                dtype={"year": "string"}, chunksize=250_000, engine="c",
            ):
                rows += len(chunk)
                yr = pd.to_numeric(chunk["year"], errors="coerce")
                chunk = chunk.assign(y=yr)
                chunk = chunk[chunk.y.notna() & chunk.y.between(YEAR_MIN, YEAR_MAX)]
                years.update(int(y) for y in chunk.y.unique())
                geo = chunk.dropna(subset=["decimalLatitude", "decimalLongitude"])
                n_nogeom += len(chunk) - len(geo)
                if len(geo) == 0:
                    continue
                la, lo = geo.decimalLatitude.tolist(), geo.decimalLongitude.tolist()
                lat_min = float(geo.decimalLatitude.min()) if lat_min is None else min(lat_min, float(geo.decimalLatitude.min()))
                lat_max = float(geo.decimalLatitude.max()) if lat_max is None else max(lat_max, float(geo.decimalLatitude.max()))
                lon_min = float(geo.decimalLongitude.min()) if lon_min is None else min(lon_min, float(geo.decimalLongitude.min()))
                lon_max = float(geo.decimalLongitude.max()) if lon_max is None else max(lon_max, float(geo.decimalLongitude.max()))
                # per-point h3 lookup with a per-chunk cache (many repeated coords)
                cache = {}
                h7_list = []
                h5_list = []
                for lat, lon in zip(la, lo):
                    key = (lat, lon)
                    hit = cache.get(key)
                    if hit is None:
                        hit = (h3.latlng_to_cell(lat, lon, RES_FINE),
                               h3.latlng_to_cell(lat, lon, RES_COARSE))
                        cache[key] = hit
                    h7_list.append(hit[0])
                    h5_list.append(hit[1])
                # fine (res 7) counts as pure ints
                geo = geo.assign(h7=h7_list)
                for k7, cnt in geo.groupby("h7").size().items():
                    kf = (di, k7)
                    hex_fine[kf] = hex_fine.get(kf, 0) + int(cnt)
                # coarse (res 5) aggregation via groupby
                geo = geo.assign(h5=h5_list)
                for k5, cnt in geo.groupby("h5").size().items():
                    kc = (di, k5)
                    hex_coarse[kc] = hex_coarse.get(kc, 0) + int(cnt)
                for yy, cnt in chunk.groupby("y").size().items():
                    tk = (di, int(yy))
                    ts[tk] = ts.get(tk, 0) + int(cnt)
            n_events = rows

        datasets.append({
            "id": dsid,
            "title": title or f"Dataset {dsid}",
            "bbox": bbox_txt,
            "events": n_events,
            "occurrences": n_occ,
            "noGeom": n_nogeom,
            "minLat": None if lat_min is None else round(lat_min, 5),
            "maxLat": None if lat_max is None else round(lat_max, 5),
            "minLon": None if lon_min is None else round(lon_min, 5),
            "maxLon": None if lon_max is None else round(lon_max, 5),
            "yearMin": min(years) if years else None,
            "yearMax": max(years) if years else None,
        })
        n_h7 = sum(1 for k in hex_fine if k[0] == di)
        n_h5 = sum(1 for k in hex_coarse if k[0] == di)
        print(f"{dsid}: {n_events} events, {n_occ} occ, hex5={n_h5}, hex7={n_h7}", flush=True)

    OUT_DIR.mkdir(exist_ok=True)

    meta = {"datasets": datasets, "resFine": RES_FINE, "resCoarse": RES_COARSE}
    (OUT_DIR / "overview_meta.json").write_text(json.dumps(meta, indent=1))
    fine_out, coarse_out, ts_out = {}, {}, {}
    for (di, hidx), cnt in hex_fine.items():
        fine_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, hidx), cnt in hex_coarse.items():
        coarse_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, yy), cnt in ts.items():
        ts_out.setdefault(str(di), {})[str(yy)] = cnt

    (OUT_DIR / "overview_map_fine.json").write_text(json.dumps(fine_out, separators=(",", ":")))
    map_coarse = {"coarse": coarse_out, "ts": ts_out}
    (OUT_DIR / "overview_map_coarse.json").write_text(json.dumps(map_coarse, separators=(",", ":")))

    # also emit .js globals so the page works when opened directly via file://
    globals = {"overview_meta": "window.OVERVIEW_META",
               "overview_map_coarse": "window.OVERVIEW_COARSE",
               "overview_map_fine": "window.OVERVIEW_FINE"}
    for name, g in globals.items():
        obj = {"overview_meta": meta,
               "overview_map_coarse": map_coarse,
               "overview_map_fine": fine_out}[name]
        (OUT_DIR / f"{name}.js").write_text(
            g + " = " + json.dumps(obj, separators=(",", ":")) + ";", encoding="utf-8")
    print("wrote .js data files for file:// usage")

    n_fine = sum(len(v) for v in fine_out.values())
    n_coarse = sum(len(v) for v in coarse_out.values())
    print(f"TOTAL hex cells: res5={n_coarse}, res7={n_fine}")


if __name__ == "__main__":
    main()
