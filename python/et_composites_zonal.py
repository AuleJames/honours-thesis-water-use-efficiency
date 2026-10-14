"""
Annual ET composites and per-field zonal statistics.

For each ET product and field-boundary survey year, monthly ET rasters are grouped by
data year and combined into annual sum and mean composites. Zonal statistics are then
computed for every field layer from the matching survey year. The results are joined to
yield data to calculate water use efficiency (WUE, kg/m3).

Rasters are accumulated one at a time, so memory stays at a few arrays per composite
however many months are stacked.

Usage:
    python et_composites_zonal.py --data-root ../data --out-root ../outputs/et_composites
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterstats import zonal_stats

# ET product name -> folder under <data-root>/et. Each product folder holds one
# subfolder per field-boundary survey year (named with the year).
ET_SOURCES = {
    "WAPOR_100m": "wapor_100m",
    "FRUITLOOK_20m": "fruitlook_20m",
    "UPSCALED_20m": "upscaled_20m",
}

# Field attribute names (matched case-insensitively)
ATTR_FARM_ID = "FARM_ID"
ATTR_AREA_HA = "AREAHA"
ATTR_CATNAME = "CATNAME"
ATTR_CROP = "CR_DETAIL"
ATTR_SUBREGION = "subregion"

ZONAL_STATS = ["mean", "sum"]

# Field layers use plural crop names, the yield table singular ones
CROP_ALIASES = {
    "apples": "apple", "pears": "pear", "plums": "plum", "peaches": "peach",
    "nectarines": "nectarine", "apricots": "apricot",
}

YEAR_RE = re.compile(r"20\d{2}")


def log(msg):
    print(msg, flush=True)


def infer_year(text):
    m = YEAR_RE.search(text)
    return int(m.group(0)) if m else None


def normalise_crop(series):
    crops = series.astype(str).str.strip().str.lower()
    return crops.replace(CROP_ALIASES)


def survey_folders(source_folder):
    """Survey-year subfolders of an ET product folder, or the folder itself if it has none."""
    subs = sorted(p for p in source_folder.iterdir() if p.is_dir())
    return subs or [source_folder]


def rasters_by_year(folder):
    """Group all .tif files under folder by data year (from the filename, else the parent folder)."""
    groups = defaultdict(list)
    for path in sorted(folder.rglob("*.tif")):
        year = infer_year(path.name) or infer_year(path.parent.name)
        if year is None:
            log(f"  WARN: no year found for {path.name}, skipping")
            continue
        groups[year].append(path)
    return dict(groups)


def annual_sum_mean(raster_paths):
    """
    Pixel-wise sum and mean of the rasters, ignoring nodata. Pixels with no valid value in
    any month are NaN in both outputs. Returns (sum, mean, profile) or (None, None, None).
    """
    profile = None
    for path in raster_paths:
        try:
            with rasterio.open(path) as src:
                profile = src.profile.copy()
            break
        except rasterio.errors.RasterioIOError as e:
            log(f"   WARN: cannot open {path.name}: {e}")
    if profile is None:
        return None, None, None

    total = np.zeros((profile["height"], profile["width"]), dtype=np.float64)
    counts = np.zeros(total.shape, dtype=np.int32)

    for path in raster_paths:
        try:
            with rasterio.open(path) as src:
                arr = src.read(1).astype(np.float32)
                invalid = np.isnan(arr) if src.nodata is None else (arr == src.nodata)
        except rasterio.errors.RasterioIOError as e:
            log(f"   WARN: cannot read {path.name}: {e}")
            continue
        valid = ~invalid & np.isfinite(arr)
        total[valid] += arr[valid]
        counts[valid] += 1

    has_data = counts > 0
    sum_arr = np.full(total.shape, np.nan, dtype=np.float32)
    mean_arr = np.full(total.shape, np.nan, dtype=np.float32)
    sum_arr[has_data] = total[has_data]
    mean_arr[has_data] = total[has_data] / counts[has_data]

    profile.update(count=1, dtype="float32", nodata=np.nan)
    return sum_arr, mean_arr, profile


def write_tif(arr, profile, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(arr, 1)


def zonal_frame(arr, profile, gdf, prefix):
    stats = zonal_stats(gdf, arr, affine=profile["transform"], stats=ZONAL_STATS, nodata=np.nan)
    return pd.DataFrame(stats).add_prefix(prefix)


def layers_by_survey_year(gdb_path):
    """Map survey year -> field layer names, using the year in each layer name."""
    mapping = defaultdict(list)
    for layer in fiona.listlayers(str(gdb_path)):
        mapping[infer_year(layer) or "unknown"].append(layer)
    return dict(mapping)


def find_column(columns, *keywords):
    """First column whose lower-cased name contains all keywords."""
    return next((c for c in columns if all(k in c.lower() for k in keywords)), None)


def load_yield(path):
    """Yield table with normalised sub_region / year / crop keys, or None if unavailable."""
    if not path.exists():
        log(f"No yield table at {path}; WUE will not be calculated")
        return None
    df = pd.read_csv(path)
    cols = {
        "sub": find_column(df.columns, "sub", "region"),
        "year": find_column(df.columns, "year"),
        "crop": find_column(df.columns, "fruit") or find_column(df.columns, "crop"),
        "yield": find_column(df.columns, "yield"),
    }
    if None in cols.values():
        log(f"Yield table is missing expected columns ({cols}); WUE will not be calculated")
        return None
    return pd.DataFrame({
        "sub_key": df[cols["sub"]].astype(str).str.strip(),
        "year": pd.to_numeric(df[cols["year"]], errors="coerce").astype("Int64"),
        "crop_key": normalise_crop(df[cols["crop"]]),
        "Yield_T_per_ha": df[cols["yield"]],
    }).drop_duplicates(["sub_key", "year", "crop_key"])


def field_attributes(gdf):
    """Field ID, area, region and crop columns with standard names."""
    cols = {c.lower(): c for c in gdf.columns}
    id_col = cols.get(ATTR_FARM_ID.lower()) or cols.get("objectid")

    out = pd.DataFrame(index=gdf.index)
    out["farm_id"] = gdf[id_col] if id_col else gdf.index
    if ATTR_AREA_HA.lower() in cols:
        out["area_ha"] = gdf[cols[ATTR_AREA_HA.lower()]]
    else:
        out["area_ha"] = gdf.geometry.area / 10_000  # assumes a metric projected CRS

    def text_col(name):
        return gdf[cols[name.lower()]].astype(str).str.strip() if name.lower() in cols else ""

    out["catname"] = text_col(ATTR_CATNAME)
    out["crop"] = normalise_crop(gdf[cols[ATTR_CROP.lower()]]) if ATTR_CROP.lower() in cols else ""
    out["subregion"] = text_col(ATTR_SUBREGION) if ATTR_SUBREGION.lower() in cols else out["catname"]
    return out.reset_index(drop=True)


def field_stats(gdf, sum_arr, mean_arr, profile, yield_df, data_year):
    out = pd.concat([
        field_attributes(gdf),
        zonal_frame(mean_arr, profile, gdf, "mean_raster_"),
        zonal_frame(sum_arr, profile, gdf, "sum_raster_"),
    ], axis=1)

    out["ET_annual_sum_mm"] = out["sum_raster_mean"].astype(float)
    out["ET_mean_monthly_mm"] = out["mean_raster_mean"].astype(float)
    out["ET_annual_sum_m3"] = out["ET_annual_sum_mm"] * out["area_ha"] * 10  # 1 mm over 1 ha = 10 m3

    if yield_df is not None:
        keys = pd.DataFrame({
            "sub_key": out["subregion"],
            "year": pd.array([data_year] * len(out), dtype="Int64"),
            "crop_key": out["crop"],
        })
        out["Yield_T_per_ha"] = keys.merge(yield_df, how="left")["Yield_T_per_ha"].to_numpy()
    else:
        out["Yield_T_per_ha"] = np.nan

    et = out["ET_annual_sum_mm"]
    out["WUE_kg_per_m3"] = np.where(et > 0, out["Yield_T_per_ha"] * 100 / et, np.nan)
    return out


def process_source(source_key, source_folder, gdb, layers_by_year, yield_df, out_root):
    results = []
    for survey_folder in survey_folders(source_folder):
        survey_year = infer_year(survey_folder.name)
        tag = f"survey_{survey_year}" if survey_year else survey_folder.name
        log(f"  {survey_folder.name} (survey year {survey_year})")

        groups = rasters_by_year(survey_folder)
        if not groups:
            log("    No rasters found")
            continue

        field_layers = layers_by_year.get(survey_year) or layers_by_year.get("unknown", [])
        out_dir = out_root / source_key / tag
        log(f"    {len(field_layers)} field layers")

        for data_year, paths in sorted(groups.items()):
            log(f"    {data_year}: {len(paths)} rasters")
            sum_arr, mean_arr, profile = annual_sum_mean(paths)
            if sum_arr is None:
                continue

            write_tif(sum_arr, profile, out_dir / "composites" / f"{source_key}_{tag}_{data_year}_annual_sum.tif")
            write_tif(mean_arr, profile, out_dir / "composites" / f"{source_key}_{tag}_{data_year}_annual_mean.tif")

            for layer in field_layers:
                gdf = gpd.read_file(gdb, layer=layer)
                if profile.get("crs") and gdf.crs and gdf.crs != profile["crs"]:
                    gdf = gdf.to_crs(profile["crs"])

                stats = field_stats(gdf, sum_arr, mean_arr, profile, yield_df, data_year)
                stats["et_source"] = source_key
                stats["survey_year"] = survey_year
                stats["data_year"] = data_year

                csv_path = out_dir / "zonal_stats" / f"zonal_{source_key}_{tag}_{layer}_{data_year}.csv"
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                stats.to_csv(csv_path, index=False)
                results.append(stats)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=Path("../data"))
    parser.add_argument("--out-root", type=Path, default=Path("../outputs/et_composites"))
    args = parser.parse_args()

    gdb = next((p for p in (args.data_root / "field_boundaries").glob("*.gdb") if p.is_dir()), None)
    if gdb is None:
        raise SystemExit(f"No .gdb with field layers found in {args.data_root / 'field_boundaries'}")
    layers_by_year = layers_by_survey_year(gdb)
    log(f"Field layers by survey year: { {k: len(v) for k, v in layers_by_year.items()} }")

    yield_df = load_yield(args.data_root / "yield" / "yield_data.csv")

    results = []
    for source_key, folder_name in ET_SOURCES.items():
        source_folder = args.data_root / "et" / folder_name
        log(f"\n{source_key}")
        if not source_folder.exists():
            log("  Folder missing, skipping")
            continue
        results += process_source(source_key, source_folder, gdb, layers_by_year, yield_df, args.out_root)

    if results:
        out_csv = args.out_root / "zonal_all_sources.csv"
        pd.concat(results, ignore_index=True).to_csv(out_csv, index=False)
        log(f"\nSaved {out_csv}")
    else:
        log("\nNo zonal statistics produced")


if __name__ == "__main__":
    main()
