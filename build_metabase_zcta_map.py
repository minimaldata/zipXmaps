#!/usr/bin/env python3
"""Build Metabase-compatible custom region-map GeoJSON from Census ZCTAs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable


CENSUS_ZCTA_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2020/shp/"
    "cb_2020_us_zcta520_500k.zip"
)
METABASE_SIZE_LIMIT_MB = 5.0
DEFAULT_CACHE_DIR = Path(".cache") / "census"
ZCTA_COLUMN_CANDIDATES = (
    "ZCTA5CE20",
    "GEOID20",
    "ZCTA5CE10",
    "GEOID10",
    "ZCTA5CE",
    "GEOID",
    "ZCTA5",
    "ZIP5",
    "ZIP",
)


class CliError(RuntimeError):
    """Expected command-line failure with a concise user-facing message."""


def require_dependencies():
    missing: list[str] = []

    try:
        import geopandas as gpd  # noqa: F401
    except ImportError:
        missing.append("geopandas")

    try:
        import pandas as pd  # noqa: F401
    except ImportError:
        missing.append("pandas")

    try:
        import requests  # noqa: F401
    except ImportError:
        missing.append("requests")

    try:
        import shapely  # noqa: F401
    except ImportError:
        missing.append("shapely")

    try:
        import pyogrio  # noqa: F401
    except ImportError:
        try:
            import fiona  # noqa: F401
        except ImportError:
            missing.append("pyogrio or fiona")

    if missing:
        deps = ", ".join(sorted(missing))
        raise CliError(
            f"Missing required dependencies: {deps}. "
            "Install them with: pip install -r requirements.txt"
        )

    import geopandas as gpd
    import pandas as pd

    return gpd, pd


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create Metabase-ready ZIP5, ZIP4-prefix, ZIP3-prefix, or ZIP2-prefix "
            "GeoJSON maps from Census ZCTA boundaries."
        )
    )
    parser.add_argument(
        "--input-shapefile",
        type=Path,
        help="Local Census ZCTA shapefile, zipped shapefile, GeoJSON, or other GeoPandas-readable path.",
    )
    parser.add_argument(
        "--download",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Download the Census cartographic ZCTA file when no input is supplied. Default: true.",
    )
    parser.add_argument(
        "--zip-csv",
        type=Path,
        help="Optional CSV containing ZIP5 values to filter the map to.",
    )
    parser.add_argument(
        "--zip-col",
        default="zip5",
        help="Column in --zip-csv containing ZIP5 values. Default: zip5.",
    )
    parser.add_argument(
        "--level",
        choices=("zip5", "zip4", "zip3", "zip2"),
        default="zip4",
        help="Aggregation level for the output map. Default: zip4.",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.001,
        help="Geometry simplification tolerance in degrees. Default: 0.001.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output GeoJSON path.",
    )
    parser.add_argument(
        "--minimize-properties",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep only the Metabase identifier and name properties. Default: true.",
    )
    parser.add_argument(
        "--drop-empty-geometries",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Drop null or empty geometries. Default: true.",
    )
    parser.add_argument(
        "--include-summary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print feature count, output size, and Metabase setup instructions. Default: true.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="Directory used for downloaded Census files. Default: .cache/census.",
    )
    return parser.parse_args(argv)


def download_census_zcta(cache_dir: Path) -> Path:
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / Path(CENSUS_ZCTA_URL).name
    extract_dir = cache_dir / zip_path.stem

    if not zip_path.exists():
        print(f"Downloading Census ZCTA cartographic boundaries: {CENSUS_ZCTA_URL}")
        with requests.get(CENSUS_ZCTA_URL, stream=True, timeout=60) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(
                dir=cache_dir, suffix=".zip", delete=False
            ) as tmp:
                tmp_path = Path(tmp.name)
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        tmp.write(chunk)
        tmp_path.replace(zip_path)

    extract_dir.mkdir(parents=True, exist_ok=True)
    shapefiles = list(extract_dir.glob("*.shp"))
    if not shapefiles:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        shapefiles = list(extract_dir.glob("*.shp"))

    if not shapefiles:
        raise CliError(f"No shapefile found after extracting {zip_path}")

    return shapefiles[0]


def find_zcta_column(columns: Iterable[str]) -> str:
    exact = {column.upper(): column for column in columns}
    for candidate in ZCTA_COLUMN_CANDIDATES:
        if candidate in exact:
            return exact[candidate]

    for column in columns:
        normalized = column.upper()
        if "ZCTA" in normalized and ("5" in normalized or "GEOID" in normalized):
            return column

    for column in columns:
        normalized = column.upper()
        if normalized.startswith("GEOID"):
            return column

    raise CliError(
        "Could not identify a ZCTA column. Expected one of: "
        + ", ".join(ZCTA_COLUMN_CANDIDATES)
    )


def normalize_zip_value(value: object) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "na", "<na>"}:
        return None

    text = re.sub(r"\.0$", "", text)
    match = re.search(r"\d{5}", text)
    if match:
        return match.group(0)

    digits = re.sub(r"\D", "", text)
    if not digits:
        return None

    if len(digits) > 5:
        digits = digits[:5]

    return digits.zfill(5)


def normalize_zip_series(series) -> "object":
    return series.map(normalize_zip_value)


def read_filter_zips(pd, zip_csv: Path, zip_col: str) -> set[str]:
    if not zip_csv.exists():
        raise CliError(f"ZIP CSV does not exist: {zip_csv}")

    frame = pd.read_csv(zip_csv, dtype={zip_col: "string"})
    if zip_col not in frame.columns:
        raise CliError(
            f"ZIP CSV column '{zip_col}' not found. Available columns: "
            + ", ".join(frame.columns.astype(str))
        )

    zips = set(normalize_zip_series(frame[zip_col]).dropna())
    if not zips:
        raise CliError(f"No usable ZIP5 values found in {zip_csv} column '{zip_col}'.")

    return zips


def fix_invalid_geometry(geometry):
    if geometry is None or geometry.is_empty or geometry.is_valid:
        return geometry

    try:
        from shapely import make_valid

        return make_valid(geometry)
    except ImportError:
        return geometry.buffer(0)


def clean_geometries(gdf, drop_empty_geometries: bool):
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.map(fix_invalid_geometry)
    if drop_empty_geometries:
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    return gdf


def load_zcta_boundaries(gpd, input_path: Path | None, download: bool, cache_dir: Path):
    if input_path is None:
        if not download:
            raise CliError(
                "No --input-shapefile supplied and --no-download was set. "
                "Provide an input path or allow download."
            )
        input_path = download_census_zcta(cache_dir)
    elif not input_path.exists():
        raise CliError(f"Input path does not exist: {input_path}")

    print(f"Loading ZCTA boundaries from {input_path}")
    gdf = gpd.read_file(input_path)
    if gdf.empty:
        raise CliError(f"No features found in input: {input_path}")

    zcta_col = find_zcta_column(gdf.columns)
    gdf = gdf[[zcta_col, "geometry"]].copy()
    gdf["zip5"] = normalize_zip_series(gdf[zcta_col])
    gdf = gdf[gdf["zip5"].notna()].copy()
    gdf = gdf[["zip5", "geometry"]]

    if gdf.empty:
        raise CliError(f"No valid ZCTA values found in column '{zcta_col}'.")

    if gdf.crs is None:
        print("Warning: input CRS is missing; assuming EPSG:4326.")
        gdf = gdf.set_crs("EPSG:4326")
    else:
        gdf = gdf.to_crs("EPSG:4326")

    return gdf


def filter_boundaries(gdf, filter_zips: set[str] | None):
    if filter_zips is None:
        return gdf

    available = set(gdf["zip5"])
    missing = sorted(filter_zips - available)
    if missing:
        preview = ", ".join(missing[:20])
        suffix = "" if len(missing) <= 20 else f", ... ({len(missing)} total)"
        print(f"Warning: {len(missing)} ZIPs from CSV did not match Census ZCTAs: {preview}{suffix}")

    filtered = gdf[gdf["zip5"].isin(filter_zips)].copy()
    if filtered.empty:
        raise CliError(
            "ZIP filter left zero matching ZCTAs. Check the ZIP column and values."
        )

    print(f"Filtered to {len(filtered):,} matching ZIP5/ZCTA features.")
    return filtered


def build_level(gdf, level: str, drop_empty_geometries: bool):
    gdf = gdf.copy()

    if level == "zip5":
        gdf = gdf.drop_duplicates(subset=["zip5"]).copy()
        gdf["name"] = gdf["zip5"]
        return gdf[["zip5", "name", "geometry"]]

    region_col = level
    prefix_length_by_level = {
        "zip4": 4,
        "zip3": 3,
        "zip2": 2,
    }
    prefix_length = prefix_length_by_level[level]
    gdf[region_col] = gdf["zip5"].str[:prefix_length]
    gdf["name"] = gdf[region_col]
    dissolved = gdf.dissolve(by=region_col, as_index=False, aggfunc={"name": "first"})
    dissolved = clean_geometries(dissolved, drop_empty_geometries)
    dissolved["name"] = dissolved[region_col]
    return dissolved[[region_col, "name", "geometry"]]


def simplify_geometries(gdf, tolerance: float, drop_empty_geometries: bool):
    if tolerance < 0:
        raise CliError("--simplify-tolerance must be zero or positive.")

    gdf = clean_geometries(gdf, drop_empty_geometries)
    if tolerance > 0:
        gdf = gdf.copy()
        gdf["geometry"] = gdf.geometry.simplify(
            tolerance=tolerance,
            preserve_topology=True,
        )
        gdf = clean_geometries(gdf, drop_empty_geometries)

    if gdf.empty:
        raise CliError("All geometries were dropped during cleanup/simplification.")

    return gdf


def assert_output_path(path: Path):
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.exists():
        raise CliError(f"Output directory does not exist: {parent}")
    if parent.is_file():
        raise CliError(f"Output directory path is a file: {parent}")


def write_minified_geojson(gdf, output_path: Path):
    geojson = json.loads(gdf.to_json(drop_id=True, na="drop"))
    with output_path.open("w", encoding="utf-8") as output:
        json.dump(geojson, output, ensure_ascii=False, separators=(",", ":"))
        output.write("\n")


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def sql_example(level: str) -> str:
    if level == "zip5":
        return """select
  lpad(zip5::varchar, 5, '0') as zip5,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;"""

    if level == "zip4":
        return """select
  left(lpad(zip5::varchar, 5, '0'), 4) as zip4,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;"""

    if level == "zip3":
        return """select
  left(lpad(zip5::varchar, 5, '0'), 3) as zip3,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;"""

    return """select
  left(lpad(zip5::varchar, 5, '0'), 2) as zip2,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;"""


def print_summary(level: str, feature_count: int, size_mb: float, output_path: Path):
    print()
    print("Build summary")
    print(f"- Output: {output_path}")
    print(f"- Level: {level}")
    print(f"- Features: {feature_count:,}")
    print(f"- Size: {size_mb:.2f} MB")

    if size_mb > METABASE_SIZE_LIMIT_MB:
        print(
            "Warning: This may be too large for Metabase custom maps. "
            "Try filtering to fewer ZIPs, using ZIP4/ZIP3, or increasing "
            "simplification tolerance."
        )

    print()
    print("Metabase setup")
    print("- Admin -> Settings -> Maps -> Custom Maps")
    print("- Map URL: hosted URL of this GeoJSON")
    print(f"- Region identifier: {level}")
    print("- Region display name: name")
    print()
    print("Example SQL")
    print(sql_example(level))
    print()
    print("Snowflake example SQL")
    print(sql_example(level))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        gpd, pd = require_dependencies()
        assert_output_path(args.out)

        filter_zips = None
        if args.zip_csv:
            filter_zips = read_filter_zips(pd, args.zip_csv, args.zip_col)
            print(f"Read {len(filter_zips):,} ZIP5 values from {args.zip_csv}")

        gdf = load_zcta_boundaries(
            gpd=gpd,
            input_path=args.input_shapefile,
            download=args.download,
            cache_dir=args.cache_dir,
        )
        gdf = clean_geometries(gdf, args.drop_empty_geometries)
        gdf = filter_boundaries(gdf, filter_zips)
        result = build_level(gdf, args.level, args.drop_empty_geometries)
        result = simplify_geometries(
            result,
            tolerance=args.simplify_tolerance,
            drop_empty_geometries=args.drop_empty_geometries,
        )

        id_col = args.level
        if args.minimize_properties:
            result = result[[id_col, "name", "geometry"]].copy()

        result = result.to_crs("EPSG:4326")
        write_minified_geojson(result, args.out)
        size_mb = file_size_mb(args.out)

        if args.include_summary:
            print_summary(args.level, len(result), size_mb, args.out)
        elif size_mb > METABASE_SIZE_LIMIT_MB:
            print(
                "Warning: This may be too large for Metabase custom maps. "
                "Try filtering to fewer ZIPs, using ZIP4/ZIP3, or increasing "
                "simplification tolerance."
            )

        return 0
    except CliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
