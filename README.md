# Water Use Efficiency of Deciduous Fruit from Satellite ET

Code from my BScHons Geoinformatics thesis (Stellenbosch University, 2025). The thesis estimates **water use efficiency (WUE = yield / evapotranspiration)** for pome and stone fruit orchards across nine sub-regions of the Western Cape, South Africa, using satellite-derived actual evapotranspiration (ET).

The pipeline turns stacks of monthly ET rasters into growing-season water use for each field. It then aggregates the fields by sub-region and crop and links them to yield records. This shows how efficiency varies between regions, crops and seasons.

## What it does

```
monthly ET rasters ──► seasonal / annual composites ──► zonal statistics per field
                                                              │
field boundaries (2013 / 2017 / 2023 surveys) ────────────────┤
sub-region boundaries ───────────────► spatial join ──────────┤
                                                              ▼
                              ET per sub-region × crop × harvest year
                                                              │
yield records ──────────────────────────────► join ───────────┴──► WUE (kg/m³), tables, trend plots
```

- **Multi-source ET:** WaPOR 100 m (2018–2025) and FruitLook 20 m (2014–2018), each handled to suit its temporal coverage.
- **Harvest-year logic:** the growing season runs September–April, so a season spans two calendar years. Complete seasons are detected automatically from the raster filenames.
- **Changing field boundaries:** each harvest year uses the field-boundary survey closest in time (2013, 2017 or 2023).
- **Robust joins:** sub-region and crop names are normalised (case, spacing, plural/singular) before ET is joined to yield records.
- **Memory-conscious compositing (Python):** rasters are accumulated one at a time rather than stacked in memory.

## Repository layout

| Path | Description |
|---|---|
| `R/01_wapor_wue.qmd` | Main analysis. WaPOR 100 m seasonal ET, WUE per sub-region and crop, trend plots |
| `R/02_fruitlook_wue.qmd` | The same workflow for the 20 m FruitLook ET of earlier seasons |
| `R/utils.R` | Shared helpers: crop name mapping, join keys, closest-survey lookup, WUE, outputs |
| `python/et_composites_zonal.py` | Annual sum/mean composites for every ET product, with per-field zonal statistics |

## Methods in brief

1. **Seasonal ET:** the monthly ET rasters for September–April are summed to give total seasonal water use (mm).
2. **Field extraction:** the mean seasonal ET is extracted for every orchard polygon.
3. **Aggregation:** fields are assigned to sub-regions and averaged per sub-region and crop.
4. **WUE:** yield (t/ha) is divided by seasonal ET and reported in **kg/m³**. 1 t/ha = 1000 kg/ha and 1 mm of ET over 1 ha = 10 m³, so WUE = yield × 100 / ET.

## Data

The input data is not included. The field boundaries, sub-regions and yield records were provided for research use and cannot be redistributed. WaPOR ET is publicly available from the [FAO WaPOR portal](https://data.apps.fao.org/wapor/).

To run the code on your own data, use this layout:

```
data/
├── et/
│   ├── wapor_100m/<survey year>/*.tif      # monthly ET, dated YYYY_MM or YYYYMMDD in filename
│   └── fruitlook_20m/<survey year>/*.tif   # monthly ET, e.g. october_2014 or rYYYY_MM in filename
├── field_boundaries/
│   ├── field_boundaries_2013.shp           # orchard polygons with a CROPS attribute
│   ├── field_boundaries_2017.shp
│   ├── field_boundaries_2023.shp
│   └── <name>.gdb                          # per-region field layers (Python script)
├── subregions/subregions.shp               # sub-region polygons with a Label attribute
└── yield/yield_data.csv                    # Sub-region, Year, Fruit Type, Yield (Tons/Ha)
```

## Running

**R (Quarto notebooks):** requires R ≥ 4.1 and Quarto.

```r
install.packages(c("terra", "sf", "dplyr", "stringr", "readr", "janitor",
                   "ggplot2", "patchwork", "openxlsx"))
```

```bash
quarto render R/01_wapor_wue.qmd
```

**Python:**

```bash
pip install -r requirements.txt
cd python
python et_composites_zonal.py --data-root ../data --out-root ../outputs/et_composites
```

Results are written to `outputs/`: CSV summaries, a results workbook, composite GeoTIFFs and trend plots.

## Tech

R (terra, sf, dplyr, ggplot2, Quarto), Python (GeoPandas, Rasterio, rasterstats, NumPy, pandas), raster compositing, zonal statistics, spatial joins.

## Author

Aule James, BScHons Geoinformatics, Stellenbosch University
