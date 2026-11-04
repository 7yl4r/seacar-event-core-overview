#!/usr/bin/env python3
"""Aggregate Event-Core data across all SEACAR DwC-A datasets into JSON bundles
for the overview HTML pages.

Aggregation outputs (H3 hexagons for res 5 & 7 + per-year counts):
  - ALL events (index page)
  - SEAGRASS events, selected by the AphiaIDs (WoRMS LSIDs) carried
    in occurrence.txt scientificNameID (seagrass page)

Selection rule for a *seagrass event*: the event has at least one occurrence row
whose scientificNameID parses to an AphiaID in the seagrass set. The seagrass
AphiaID set is itself derived from occurrence.txt: every unique
(scientificNameID, scientificName) pair whose name is a known seagrass taxon
contributes its AphiaID; rows with a blank ID but a seagrass name are mapped
through that name→AphiaID table (blank IDs are also resolved through the
WoRMS REST API when needed and cached).
"""

import json
import re
import time
import urllib.request
from pathlib import Path

import h3
import pandas as pd

DWC_DIR = Path("../seacar2dwc/data/dwc")
OUT_DIR = Path("output")

RES_FINE = 7    # ~5.2 km² hexes
RES_COARSE = 5  # ~177 km² hexes
YEAR_MIN, YEAR_MAX = 1900, 2030

SEAGRASS_NAME_RE = re.compile(
    r"halophila|halodule|thalassia testudinum|syringodium|ruppia maritima|"
    r"zostera|posidonia|cymodocea|amphibolis|thalassodendron|enhalus|phyllospadix|seagrass",
    re.I)

LSID_RE = re.compile(r"taxname:(\d+)")
APHIA_CACHE = OUT_DIR / ".aphia_cache.json"

ts = {}          # (ds_idx, year) -> [total, seagrass]
datasets = []
hex_fine = {}       # all events
hex_coarse = {}
hex_fine_seag = {}  # seagrass-occurrence events only
hex_coarse_seag = {}
aphia_registry = {}   # aphiaID(int) -> [name, name-source]
name_registry = {}    # name -> aphiaID or None


def load_aphia_cache():
    try:
        APHIA_CACHE.parent.mkdir(exist_ok=True)
        return json.loads(APHIA_CACHE.read_text())
    except Exception:
        return {}


def save_aphia_cache(c):
    APHIA_CACHE.write_text(json.dumps(c, indent=1, sort_keys=True))


def worms_lookup(name, cache):
    """Resolve a scientific name to its AphiaID via the WoRMS REST API (cached)."""
    if name in cache:
        return cache[name]
    url = "https://www.marinespecies.org/rest/AphiaRecordsByName/" + urllib.request.quote(name)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            recs = json.loads(resp.read())
        aphia = recs[0].get("AphiaID") if recs else None
    except Exception as e:
        print(f"  WoRMS lookup failed for {name!r}: {e}", flush=True)
        aphia = None
        time.sleep(2)
    cache[name] = aphia
    return aphia


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-occurrence-scan", action="store_true",
                    help="reuse cached seagrass AphiaID registry instead of scanning occurrence files")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    cache = load_aphia_cache()

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
        n_seag_events = 0
        n_seag_occ = 0
        seag_event_ids = set()
        years = set()
        lat_min = lat_max = lon_min = lon_max = None
        n_nogeom = 0

        # ---- pass 1: occurrences -> seagrass eventID set (selection by AphiaID)
        if not args.skip_occurrence_scan and occ_path.exists():
            for chunk in pd.read_csv(
                occ_path, sep="\t",
                usecols=["eventID", "scientificName", "scientificNameID"],
                dtype=str, chunksize=500_000, engine="c",
            ):
                base = chunk[chunk.scientificName.str.contains(SEAGRASS_NAME_RE, na=False, regex=True)]
                if not len(base):
                    continue
                lsid = base.scientificNameID.str.extract(r"taxname:(\d+)")[0]
                with_id = base[lsid.notna()]
                blank = base[lsid.isna()]
                # resolve blank-ID names -> names already mapped, or via WoRMS
                to_resolve = [n for n in blank.scientificName.dropna().unique()
                              if n not in name_registry]
                for n in to_resolve:
                    name_registry[n] = worms_lookup(n, cache)
                blank_ok = blank.scientificName.map(lambda n: name_registry.get(n) is not None)
                sub = pd.concat([with_id, blank[blank_ok]])
                if len(sub):
                    seag_event_ids.update(sub.eventID.dropna())
                    n_seag_occ += int(len(sub))
                    for name, aid in sub[["scientificName", "scientificNameID"]].drop_duplicates().itertuples(index=False):
                        if isinstance(aid, str):
                            m = LSID_RE.search(aid)
                            if m:
                                aphia_registry.setdefault(int(m.group(1)), [name, "occurrence.txt"])
                                name_registry.setdefault(name, int(m.group(1)))
                        else:   # resolved blank ID
                            aid = name_registry.get(name)
                            if aid is not None:
                                aphia_registry.setdefault(aid, [name, "resolved from blank scientificNameID"])

        # ---- pass 2: events -> totals + per-year + h3 hexes
        rows = 0
        if event_path.exists():
            for chunk in pd.read_csv(
                event_path, sep="\t",
                usecols=["eventID", "decimalLatitude", "decimalLongitude", "year"],
                dtype={"year": "string"}, chunksize=250_000, engine="c",
            ):
                rows += len(chunk)
                yr = pd.to_numeric(chunk["year"], errors="coerce")
                seag = chunk.eventID.isin(seag_event_ids) if seag_event_ids else False
                chunk = chunk.assign(seag=seag, y=yr)
                chunk = chunk[chunk.y.notna() & chunk.y.between(YEAR_MIN, YEAR_MAX)]
                years.update(int(y) for y in chunk.y.unique())
                # time series applies to ALL events, mapped or not
                chunk_tot = chunk.groupby("y").size()
                chunk_seag = chunk[chunk.seag].groupby("y").size()
                for yy, cnt in chunk_tot.items():
                    tk = (di, int(yy))
                    if tk not in ts:
                        ts[tk] = [0, 0]
                    ts[tk][0] += int(cnt)
                for yy, cnt in chunk_seag.items():
                    ts[(di, int(yy))][1] += int(cnt)
                n_seag_events += int(chunk.seag.sum())
                geo = chunk.dropna(subset=["decimalLatitude", "decimalLongitude"])
                n_nogeom += len(chunk) - len(geo)
                if len(geo) == 0:
                    continue
                la = geo.decimalLatitude.tolist()
                lo = geo.decimalLongitude.tolist()
                lat_min = float(geo.decimalLatitude.min()) if lat_min is None else min(lat_min, float(geo.decimalLatitude.min()))
                lat_max = float(geo.decimalLatitude.max()) if lat_max is None else max(lat_max, float(geo.decimalLatitude.max()))
                lon_min = float(geo.decimalLongitude.min()) if lon_min is None else min(lon_min, float(geo.decimalLongitude.min()))
                lon_max = float(geo.decimalLongitude.max()) if lon_max is None else max(lon_max, float(geo.decimalLongitude.max()))
                # per-point h3 lookup with a per-chunk cache (many repeated coords)
                cache_pts = {}
                h7_list = []
                h5_list = []
                for lat, lon in zip(la, lo):
                    key = (lat, lon)
                    hit = cache_pts.get(key)
                    if hit is None:
                        hit = (h3.latlng_to_cell(lat, lon, RES_FINE),
                               h3.latlng_to_cell(lat, lon, RES_COARSE))
                        cache_pts[key] = hit
                    h7_list.append(hit[0])
                    h5_list.append(hit[1])
                # aggregate all + seagrass-only
                geo = geo.assign(h7=h7_list, h5=h5_list)
                for hcol, total_map, seag_map in (
                    ("h7", hex_fine, hex_fine_seag), ("h5", hex_coarse, hex_coarse_seag),
                ):
                    for k, cnt in geo.groupby(hcol).size().items():
                        total_map[(di, k)] = total_map.get((di, k), 0) + int(cnt)
                    sub = geo[geo.seag]
                    for k, cnt in sub.groupby(hcol).size().items():
                        seag_map[(di, k)] = seag_map.get((di, k), 0) + int(cnt)
            n_events = rows

        datasets.append({
            "id": dsid,
            "title": title or f"Dataset {dsid}",
            "bbox": bbox_txt,
            "events": n_events,
            "seagrassEvents": n_seag_events,
            "seagrassOccurrences": n_seag_occ,
            "occurrences": n_occ,
            "noGeom": n_nogeom,
            "minLat": None if lat_min is None else round(lat_min, 5),
            "maxLat": None if lat_max is None else round(lat_max, 5),
            "minLon": None if lon_min is None else round(lon_min, 5),
            "maxLon": None if lon_max is None else round(lon_max, 5),
            "yearMin": min(years) if years else None,
            "yearMax": max(years) if years else None,
        })
        n_h5 = sum(1 for k in hex_coarse if k[0] == di)
        n_h7 = sum(1 for k in hex_fine if k[0] == di)
        print(f"{dsid}: {n_events} events, {n_seag_events} seagrass-event, "
              f"{n_seag_occ} seagrass-occ, {n_occ} occ, hex5={n_h5}, hex7={n_h7}", flush=True)

    save_aphia_cache(cache)

    meta = {"datasets": datasets, "resFine": RES_FINE, "resCoarse": RES_COARSE}
    (OUT_DIR / "overview_meta.json").write_text(json.dumps(meta, indent=1))

    fine_out, coarse_out, seag_fine_out, seag_coarse_out, ts_out = {}, {}, {}, {}, {}
    for (di, hidx), cnt in hex_fine.items():
        fine_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, hidx), cnt in hex_coarse.items():
        coarse_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, hidx), cnt in hex_fine_seag.items():
        seag_fine_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, hidx), cnt in hex_coarse_seag.items():
        seag_coarse_out.setdefault(str(di), []).append([hidx, cnt])
    for (di, yy), (tot, seag) in ts.items():
        ts_out.setdefault(str(di), {})[str(yy)] = [tot, seag]

    (OUT_DIR / "overview_map_fine.json").write_text(
        json.dumps({"fine": fine_out, "seagrassFine": seag_fine_out}, separators=(",", ":")))
    map_coarse = {"coarse": coarse_out, "seagrassCoarse": seag_coarse_out, "ts": ts_out}
    (OUT_DIR / "overview_map_coarse.json").write_text(json.dumps(map_coarse, separators=(",", ":")))
    (OUT_DIR / "overview_aphiaids.json").write_text(
        json.dumps({"aphiaIds": [[str(k), v[0], v[1]] for k, v in sorted(aphia_registry.items())],
                    "unresolvedNames": [[n, i] for n, i in name_registry.items() if i is None]},
                   separators=(",", ":")))
    print(f"seagrass AphiaIDs: {len(aphia_registry)}, unresolved names: "
          f"{sum(1 for _, i in name_registry.items() if i is None)}")

    # .js globals so the pages also work when opened directly via file://
    for fname, g, obj in [
        ("overview_meta", "window.OVERVIEW_META", meta),
        ("overview_map_coarse", "window.OVERVIEW_COARSE", map_coarse),
        ("overview_map_fine", "window.OVERVIEW_FINE", {"fine": fine_out, "seagrassFine": seag_fine_out}),
        ("overview_aphiaids", "window.OVERVIEW_APPIAIDS",
         json.loads((OUT_DIR / "overview_aphiaids.json").read_text())),
    ]:
        (OUT_DIR / f"{fname}.js").write_text(
            g + " = " + json.dumps(obj, separators=(",", ":")) + ";", encoding="utf-8")

    n_fine = sum(len(v) for v in fine_out.values())
    n_coarse = sum(len(v) for v in coarse_out.values())
    n_fine_seag = sum(len(v) for v in seag_fine_out.values())
    n_coarse_seag = sum(len(v) for v in seag_coarse_out.values())
    print(f"TOTAL hex cells: res5={n_coarse} (seagrass {n_coarse_seag}), "
          f"res7={n_fine} (seagrass {n_fine_seag})")


if __name__ == "__main__":
    main()
