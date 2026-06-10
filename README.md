# Metabase ZCTA Custom Map Builder

This project builds Metabase-compatible custom region map GeoJSON files from U.S. Census ZCTA boundary data. The output is intended for Metabase regional choropleth maps where a SQL query returns a region code plus a metric.

Metabase custom maps use hosted GeoJSON files. The GeoJSON must contain a region identifier property whose values match your query output. This tool keeps the output properties simple and lowercase:

- ZIP5 / ZCTA5: `zip5`
- ZIP4-prefix: `zip4`
- ZIP3-prefix: `zip3`
- ZIP2-prefix: `zip2`
- Display name: `name`

## Geography Notes

ZIP5 output uses Census ZCTAs, not USPS ZIP Codes. ZCTAs are Census approximations of ZIP Code service areas and are the best public boundary source for ZIP5-style mapping.

ZIP4-prefix is not ZIP+4. It means the first four digits of a five-digit ZIP/ZCTA. For example, `10001`, `10002`, and `10003` all roll up to `1000`. The tool dissolves all ZCTA polygons with the same prefix into one feature. This is not an official geography, but it is often useful for keeping Metabase custom maps small enough to load.

ZIP3-prefix works the same way, using the first three digits of ZIP5.

ZIP2-prefix uses the first two digits of ZIP5. It is a very coarse rollup for whole-USA orientation maps when ZIP3 still has too many regions or too much geometry.

Full national ZIP5 GeoJSON can exceed Metabase's practical custom map size limit, commonly around 5 MB. Prefer filtering to the ZIPs present in your data, using ZIP4/ZIP3/ZIP2 aggregation, and increasing simplification tolerance when needed.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On macOS system Python 3.9, `urllib3<2` avoids the LibreSSL warning emitted by newer urllib3 releases.

## Examples

Open the exploratory HTML helper:

```bash
open index.html
```

The helper can preview generated `.geojson` files directly in the browser. Use the file picker, or serve the project directory locally and load a path such as `metabase_zip3.geojson`, to inspect feature count, loaded size, identifier/name fields, and individual region properties before hosting the map for Metabase. The preview renders the actual features in the loaded file; the built-in mini sample is only a parser/rendering check.

Generate centroid CSVs for Metabase pin or grid maps:

```bash
python3 build_metabase_centroid_csv.py --all
```

This creates:

- `metabase_zip5_centroids.csv`
- `metabase_zip4_centroids.csv`
- `metabase_zip3_centroids.csv`
- `metabase_zip2_centroids.csv`

Use a local Census cartographic boundary shapefile:

```bash
python3 build_metabase_zcta_map.py \
  --input-shapefile path/to/cb_2020_us_zcta520_500k.shp \
  --zip-csv my_zips.csv \
  --zip-col zip5 \
  --level zip4 \
  --simplify-tolerance 0.001 \
  --out metabase_zip4.geojson
```

Download the Census ZCTA cartographic boundary file automatically and build ZIP3 prefixes:

```bash
python3 build_metabase_zcta_map.py \
  --level zip3 \
  --out metabase_zip3.geojson
```

Build a very coarse national ZIP2-prefix file:

```bash
python3 build_metabase_zcta_map.py \
  --level zip2 \
  --out metabase_zip2.geojson
```

Build a filtered ZIP5 file:

```bash
python3 build_metabase_zcta_map.py \
  --zip-csv my_zips.csv \
  --zip-col zip5 \
  --level zip5 \
  --out metabase_zip5.geojson
```

## CLI Arguments

- `--input-shapefile`: optional local Census ZCTA shapefile, zipped shapefile, GeoJSON, or other GeoPandas-readable input.
- `--download` / `--no-download`: download Census cartographic ZCTA data when no input is supplied. Default: `--download`.
- `--zip-csv`: optional CSV containing ZIPs to filter to.
- `--zip-col`: column in the CSV containing ZIP5 values. Default: `zip5`.
- `--level`: `zip5`, `zip4`, `zip3`, or `zip2`. Default: `zip4`.
- `--simplify-tolerance`: geometry simplification tolerance in degrees. Default: `0.001`.
- `--out`: output GeoJSON path. Required.
- `--minimize-properties` / `--no-minimize-properties`: keep only the region identifier and `name`. Default: enabled.
- `--drop-empty-geometries` / `--no-drop-empty-geometries`: drop null or empty geometries. Default: enabled.
- `--include-summary` / `--no-include-summary`: print build summary and Metabase instructions. Default: enabled.

## ZIP Normalization

When `--zip-csv` is supplied, ZIP values are normalized before filtering:

- Leading zeroes are preserved.
- Excel-style numeric values such as `1234.0` become `01234`.
- ZIP+4-looking values such as `12345-6789` become `12345`.
- Whitespace is trimmed.

The script warns when ZIPs in the CSV do not match any Census ZCTA.

## Hosting The GeoJSON

Metabase needs a public URL for the GeoJSON. Common hosting options include:

- A public S3 object or compatible object storage URL.
- A static site host such as Netlify, Vercel, or GitHub Pages.
- Any web server that serves the file with public access.

After hosting, copy the public GeoJSON URL into Metabase.

## Metabase Setup

In Metabase:

1. Go to Admin -> Settings -> Maps -> Custom Maps.
2. Add the hosted GeoJSON URL.
3. Set the region identifier to `zip5`, `zip4`, `zip3`, or `zip2`, matching the file you generated.
4. Set the region display name to `name`.
5. In your question, return a column with the same region identifier plus a metric.

## Pin And Grid Maps

The `.geojson` outputs are for Metabase custom region maps. For zoomable pin or grid maps, use the centroid CSV outputs instead.

Each centroid CSV contains:

- `zip_level`
- `zip_code`
- the level-specific code column, such as `zip5`, `zip4`, `zip3`, or `zip2`
- `name`
- `latitude`
- `longitude`

Example `zip3` centroid rows:

```text
zip_level,zip_code,zip3,name,latitude,longitude
zip3,100,100,100,40.750000,-73.990000
```

For a pin map, join your metric query to a centroid table and return latitude and longitude:

```sql
select
  c.latitude,
  c.longitude,
  c.zip3,
  count(*) as metric
from your_table t
join zip3_centroids c
  on left(lpad(t.zip5::varchar, 5, '0'), 3) = c.zip3
where t.zip5 is not null
group by 1, 2, 3;
```

For a grid map, use the same latitude and longitude columns. Metabase will bin or aggregate nearby points depending on the map settings available in your Metabase version.

## Example SQL

ZIP5:

```sql
select
  lpad(zip5::varchar, 5, '0') as zip5,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;
```

ZIP4-prefix:

```sql
select
  left(lpad(zip5::varchar, 5, '0'), 4) as zip4,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;
```

ZIP3-prefix:

```sql
select
  left(lpad(zip5::varchar, 5, '0'), 3) as zip3,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;
```

ZIP2-prefix:

```sql
select
  left(lpad(zip5::varchar, 5, '0'), 2) as zip2,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;
```

The same expressions work in Snowflake:

```sql
select
  left(lpad(zip5::varchar, 5, '0'), 4) as zip4,
  count(*) as metric
from your_table
where zip5 is not null
group by 1;
```
