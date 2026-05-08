from __future__ import annotations

from typing import Any


def affine_from_geo_meta(geo: dict[str, Any]):
    from affine import Affine

    if geo.get("chip_transform_order") == "gdal":
        return Affine.from_gdal(*geo["chip_transform"])
    return Affine(*geo["chip_transform"])


def to_yolo_obb_line(class_index: int, obb_norm: list[float]) -> str:
    return " ".join([str(class_index)] + [f"{float(v):.6f}" for v in obb_norm])


def to_dota_line(label: str, obb_norm: list[float], width: int, height: int, difficult: int = 0) -> str:
    pts = []
    for index, value in enumerate(obb_norm):
        scale = width if index % 2 == 0 else height
        pts.append(str(int(round(float(value) * scale))))
    return " ".join(pts + [label, str(difficult)])


def to_geojson_feature(det: dict[str, Any], affine_transform, image_size: tuple[int, int], properties: dict[str, Any] | None = None) -> dict[str, Any]:
    width, height = image_size
    coords = []
    for x_norm, y_norm in zip(det["obb"][0::2], det["obb"][1::2]):
        x_geo, y_geo = affine_transform * (float(x_norm) * width, float(y_norm) * height)
        coords.append([x_geo, y_geo])
    coords.append(coords[0])
    props = {
        "class": det.get("class"),
        "original_class": det.get("original_class"),
        "confidence": det.get("confidence"),
        "provider": "sam3",
        **(properties or {}),
    }
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [coords]}}


def mask_to_map_obb_feature(mask, *, transform, src_crs, dst_crs=None, properties: dict[str, Any] | None = None) -> dict[str, Any] | None:
    from pyproj import CRS, Transformer
    from rasterio.features import shapes
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shp_transform, unary_union

    polys = []
    mask_u8 = mask.astype("uint8")
    for geom, value in shapes(mask_u8, mask=mask_u8.astype(bool), transform=transform):
        if int(value) != 1:
            continue
        poly = shape(geom).buffer(0)
        if not poly.is_empty:
            polys.append(poly)
    if not polys:
        return None

    export_crs = CRS.from_user_input(src_crs)
    work = unary_union(polys).buffer(0)
    if dst_crs:
        target = CRS.from_user_input(dst_crs)
        tx = Transformer.from_crs(export_crs, target, always_xy=True)
        work = shp_transform(tx.transform, work)
        export_crs = target
    obb = work.minimum_rotated_rectangle
    if obb.geom_type != "Polygon":
        return None
    props = {"provider": "sam3", "crs": export_crs.to_string(), **(properties or {})}
    return {"type": "Feature", "properties": props, "geometry": mapping(obb)}


def postgis_oriented_envelope_sql(mask_table: str = "sam3_masks") -> str:
    return f"SELECT id, ST_OrientedEnvelope(geom) AS obb_geom FROM {mask_table} WHERE NOT ST_IsEmpty(geom);"
