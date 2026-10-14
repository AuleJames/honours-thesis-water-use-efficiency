# Shared helpers for the WUE notebooks.

# Crop names differ between the field-boundary attribute tables (often plural) and the
# yield table (singular), so both are mapped to one canonical name before joining.
CROP_LOOKUP <- c(
  Apple = "Apple", Apples = "Apple",
  Pear = "Pear", Pears = "Pear",
  Plum = "Plum", Plums = "Plum",
  Peach = "Peach", Peaches = "Peach",
  Nectarine = "Nectarine", Nectarines = "Nectarine",
  Apricot = "Apricot", Apricots = "Apricot"
)
CROP_TYPES <- unique(unname(CROP_LOOKUP))

canonical_crop <- function(x) {
  x <- stringr::str_trim(x)
  dplyr::coalesce(unname(CROP_LOOKUP[x]), x)
}

# Normalise sub-region and crop names so joins don't fail on case, spacing or punctuation.
clean_key <- function(x) {
  stringr::str_replace_all(stringr::str_to_lower(x), "[/\\s-]+", "")
}

# Field boundaries were surveyed in these years; each harvest year uses the closest survey
# (ties go to the earlier survey).
SURVEY_YEARS <- c(2013, 2017, 2023)

closest_survey_year <- function(harvest_year, survey_years = SURVEY_YEARS) {
  survey_years[which.min(abs(survey_years - harvest_year))]
}

field_boundary_path <- function(data_root, survey_year) {
  file.path(data_root, "field_boundaries", paste0("field_boundaries_", survey_year, ".shp"))
}

# Water use efficiency in kg/m3: 1 t/ha = 1000 kg/ha, and 1 mm of ET over 1 ha = 10 m3.
wue_kg_per_m3 <- function(yield_t_ha, et_mm) {
  ifelse(!is.na(yield_t_ha) & !is.na(et_mm) & et_mm > 0, yield_t_ha * 100 / et_mm, NA_real_)
}

# Yield table with normalised join keys.
load_yield <- function(path) {
  readr::read_csv(path, show_col_types = FALSE) |>
    janitor::clean_names() |>
    dplyr::mutate(
      sub_region = stringr::str_trim(sub_region),
      fruit_type = canonical_crop(fruit_type),
      region_key = clean_key(sub_region),
      crop_key = clean_key(fruit_type)
    )
}

# Attach yield and WUE to a seasonal ET summary (one row per sub-region, crop and harvest year).
add_yield_and_wue <- function(et_summary, yield_data) {
  et_summary |>
    dplyr::mutate(region_key = clean_key(sub_region), crop_key = clean_key(crop_type)) |>
    dplyr::left_join(
      dplyr::select(yield_data, region_key, year, crop_key, yield_t_ha = yield_tons_ha),
      by = c("region_key", "yield_year" = "year", "crop_key")
    ) |>
    dplyr::mutate(wue_kg_m3 = wue_kg_per_m3(yield_t_ha, et_sept_apr_sum_mm)) |>
    dplyr::select(-region_key, -crop_key)
}

# Master table: every yield record, with the matching seasonal ET and WUE where available.
build_master_table <- function(yield_data, et_summary) {
  et_keys <- et_summary |>
    dplyr::mutate(region_key = clean_key(sub_region), crop_key = clean_key(crop_type)) |>
    dplyr::select(region_key, yield_year, crop_key, et_sept_apr_sum_mm, survey_year, et_source)

  yield_data |>
    dplyr::left_join(et_keys, by = c("region_key", "year" = "yield_year", "crop_key")) |>
    dplyr::mutate(wue_kg_m3 = wue_kg_per_m3(yield_tons_ha, et_sept_apr_sum_mm)) |>
    dplyr::select(-region_key, -crop_key) |>
    dplyr::relocate(
      sub_region, year, fruit_type, yield_tons_ha, et_sept_apr_sum_mm, wue_kg_m3,
      survey_year, et_source
    )
}

# Save the yield data, ET/WUE summary and master table as sheets in one workbook.
write_results_workbook <- function(path, yield_data, et_wue, master) {
  wb <- openxlsx::createWorkbook()
  sheets <- list(
    Yield_Data = dplyr::select(yield_data, -region_key, -crop_key),
    ET_WUE_Summary = et_wue,
    Master_Table = master
  )
  for (name in names(sheets)) {
    openxlsx::addWorksheet(wb, name)
    openxlsx::writeData(wb, name, sheets[[name]])
  }
  openxlsx::saveWorkbook(wb, path, overwrite = TRUE)
}
