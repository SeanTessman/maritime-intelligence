# Reference data (not committed)

| file | source | how to get it |
|---|---|---|
| `UpdatedPub150.csv` | NGA World Port Index (Pub. 150) | `curl.exe -L -o data\reference\UpdatedPub150.csv "https://msi.nga.mil/api/publications/download?type=view&key=16920959/SFH00000/UpdatedPub150.csv"` |
| `ne_10m_land.zip` (optional) | Natural Earth 1:10m land | https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip; the clipped result is already committed as `assets/natural_earth_ny_land.geojson` |

See `docs/data_sources.md` for provenance and terms.
