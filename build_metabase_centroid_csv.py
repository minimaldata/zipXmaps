#!/usr/bin/env python3
"""Create Metabase pin/grid-map centroid CSVs from generated GeoJSON maps."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape


LEVELS = ("zip5", "zip4", "zip3", "zip2")


class CliError(RuntimeError):
    """Expected command-line failure with a concise user-facing message."""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create centroid CSVs for Metabase pin or grid maps from "
            "Metabase-ready GeoJSON region maps."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Input GeoJSON file. Required unless --all is supplied.",
    )
    parser.add_argument(
        "--level",
        choices=LEVELS,
        help="Region level. If omitted, inferred from GeoJSON properties.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output CSV path. Required unless --all is supplied.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate centroid CSVs for metabase_zip5/4/3/2.geojson in the current directory.",
    )
    return parser.parse_args(argv)


def infer_level(properties: dict) -> str:
    for level in LEVELS:
        if level in properties:
            return level
    raise CliError("Could not infer level. Expected one of: " + ", ".join(LEVELS))


def feature_rows(input_path: Path, level: str | None):
    with input_path.open("r", encoding="utf-8") as handle:
        geojson = json.load(handle)

    if geojson.get("type") != "FeatureCollection":
        raise CliError(f"Expected FeatureCollection GeoJSON: {input_path}")

    features = geojson.get("features") or []
    if not features:
        raise CliError(f"No features found in {input_path}")

    inferred_level = level or infer_level(features[0].get("properties") or {})
    rows = []

    for index, feature in enumerate(features, start=1):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            continue

        region_code = properties.get(inferred_level)
        if region_code is None or region_code == "":
            raise CliError(
                f"Feature {index} in {input_path} is missing property '{inferred_level}'."
            )

        point = shape(geometry).representative_point()
        rows.append(
            {
                "zip_level": inferred_level,
                "zip_code": str(region_code),
                inferred_level: str(region_code),
                "name": str(properties.get("name") or region_code),
                "latitude": f"{point.y:.6f}",
                "longitude": f"{point.x:.6f}",
            }
        )

    if not rows:
        raise CliError(f"No usable polygon features found in {input_path}")

    return inferred_level, rows


def write_csv(input_path: Path, output_path: Path, level: str | None = None):
    if not input_path.exists():
        raise CliError(f"Input file does not exist: {input_path}")

    parent = output_path.parent if str(output_path.parent) else Path(".")
    if not parent.exists():
        raise CliError(f"Output directory does not exist: {parent}")

    inferred_level, rows = feature_rows(input_path, level)
    fieldnames = ["zip_level", "zip_code", inferred_level, "name", "latitude", "longitude"]

    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows):,} {inferred_level} centroids to {output_path}")


def write_all():
    for level in LEVELS:
        input_path = Path(f"metabase_{level}.geojson")
        output_path = Path(f"metabase_{level}_centroids.csv")
        write_csv(input_path, output_path, level)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        if args.all:
            write_all()
            return 0

        if not args.input or not args.out:
            raise CliError("--input and --out are required unless --all is supplied.")

        write_csv(args.input, args.out, args.level)
        return 0
    except CliError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
