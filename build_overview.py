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
hex_fine = {}      # (ds_idx, h3 res7) -> count  (all events)
hex_coarse = {}    # (ds_idx, h3 res5) -> count
hex_fine_sav = {}  # same, seagrass/SAV events only
hex_coarse_sav = {}
ts = {}            # (ds_idx, year) -> [total, sav]

# events classified as seagrass / SAV by Event-Core habitat
import re as _re
SAV_RE = _re.compile(r"submerged aquatic vegetation|seagrass", _re.I)
is_sav = lambda h: isinstance(h, str) and bool(SAV_RE.search(h))


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
        n_sav = 0
        years = set()
        lat_min = lat_max = lon_min = lon_max = None
        n_nogeom = 0

        if event_path.exists():
            rows = 0
            for chunk in pd.read_csv(
                event_path, sep="\t", usecols=["decimalLatitude", "decimalLongitude", "year", "habitat"],
                dtype={"year": "string"}, chunksize=250_000, engine="c",
            ):
                rows += len(chunk)
                yr = pd.to_numeric(chunk["year"], errors="coerce")
                sa = chunk["habitat"].map(is_sav).astype(bool)   # bool even for empty chunks
                chunk = chunk.assign(sav=sa, y=yr)
                chunk = chunk[chunk.y.notna() & chunk.y.between(YEAR_MIN, YEAR_MAX)]
                years.update(int(y) for y in chunk.y.unique())
                # time series + seagrass counts apply to ALL events, mapped or not
                chunk_tot = chunk.groupby("y").size()
                chunk_sav = chunk[chunk.sav].groupby("y").size()
                for yy, cnt in chunk_tot.items():
                    tk = (di, int(yy))
                    if tk not in ts:
                        ts[tk] = [0, 0]
                    ts[tk][0] += int(cnt)
                for yy, cnt in chunk_sav.items():
                    ts[(di, int(yy))][1] += int(cnt)
                n_sav += int(chunk.sav.sum())
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
                # fine (res 7) counts
                geo = geo.assign(h7=h7_list)
                sizes7 = geo.groupby("h7").size()
                sav7 = geo[geo.sav].groupby("h7").size()
                for k7, cnt in sizes7.items():
                    kf = (di, k7)
                    hex_fine[kf] = hex_fine.get(kf, 0) + int(cnt)
                for k7, cnt in sav7.items():
                    kfs = (di, k7)
                    hex_fine_sav[kfs] = hex_fine_sav.get(kfs, 0) + int(cnt)
                # coarse (res 5) aggregation
                geo = geo.assign(h5=h5_list)
                sizes5 = geo.groupby("h5").size()
                sav5 = geo[geo.sav].groupby("h5").size()
                for k5, cnt in sizes5.items():
                    kc = (di, k5)
                    hex_coarse[kc] = hex_coarse.get(kc, 0) + int(cnt)
                for k5, cnt in sav5.items():
                    kcs = (di, k5)
                    hex_coarse_sav[kcs] = hex_coarse_sav.get(kcs, 0) + int(cnt)
            n_events = rows

        datasets.append({
            "id": dsid,
            "title": title or f"Dataset {dsid}",
            "bbox": bbox_txt,
            "events": n_events,
            "sav": n_sav,
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
        print(f"{dsid}: {n_events} events ({n_sav} SAV), {n_occ} occ, hex5={n_h5}, hex7={n_h7}", flush=True)

    OUT_DIR.mkdir(exist_ok=True)

    meta = {"datasets": datasets, "resFine": RES_FINE, "resCoarse": RES_COARSE}
    (OUT_DIR / "overview_meta.json").write_text(json.dumps(meta, indent=1))
    fine_out, coarse_out = {}, {}
    fine_sav_out, coarse_sav_out = {}, {}
    ts_out = {}
    for (di, hidx), cnt in hex_fine.items():
        fine_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, hidx), cnt in hex_coarse.items():
        coarse_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, hidx), cnt in hex_fine_sav.items():
        fine_sav_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, hidx), cnt in hex_coarse_sav.items():
        coarse_sav_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, yy), (tot, sav) in ts.items():
        ts_out.setdefault(str(di), {})[str(yy)] = [tot, sav]

    (OUT_DIR / "overview_map_fine.json").write_text(
        json.dumps({"fine": fine_out, "fineSav": fine_sav_out}, separators=(",", ":")))
    map_coarse = {"coarse": coarse_out, "coarseSav": coarse_sav_out, "ts": ts_out}
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
    n_fine_sav = sum(len(v) for v in fine_sav_out.values())
    n_coarse_sav = sum(len(v) for v in coarse_sav_out.values())
    print(f"TOTAL hex cells: res5={n_coarse} (sav {n_coarse_sav}), res7={n_fine} (sav {n_fine_sav})")


if __name__ == "__main__":
    main()
