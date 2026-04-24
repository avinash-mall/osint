-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- Satellite imagery catalog
CREATE TABLE IF NOT EXISTS satellite_passes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    file_path VARCHAR(1024) NOT NULL UNIQUE,
    sensor_type VARCHAR(100),
    acquisition_time TIMESTAMP WITH TIME ZONE,
    cloud_cover REAL DEFAULT 0.0,
    footprint GEOMETRY(MULTIPOLYGON, 4326),
    crs VARCHAR(50) DEFAULT 'EPSG:4326',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create spatial index on footprint
CREATE INDEX IF NOT EXISTS idx_passes_footprint ON satellite_passes USING GIST(footprint);
CREATE INDEX IF NOT EXISTS idx_passes_time ON satellite_passes(acquisition_time);

-- Detections table
CREATE TABLE IF NOT EXISTS detections (
    id SERIAL PRIMARY KEY,
    pass_id INTEGER REFERENCES satellite_passes(id) ON DELETE CASCADE,
    class VARCHAR(100) NOT NULL,
    confidence REAL NOT NULL,
    geom GEOMETRY(POLYGON, 4326),
    centroid GEOMETRY(POINT, 4326),
    pixel_bbox JSONB,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Spatial indexes
CREATE INDEX IF NOT EXISTS idx_detections_geom ON detections USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_detections_centroid ON detections USING GIST(centroid);
CREATE INDEX IF NOT EXISTS idx_detections_class ON detections(class);

-- Vector basemap data (Natural Earth will be loaded here)
CREATE TABLE IF NOT EXISTS ne_countries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    admin VARCHAR(255),
    iso_a3 VARCHAR(3),
    pop_est BIGINT,
    gdp_md_est BIGINT,
    geom GEOMETRY(MULTIPOLYGON, 4326)
);
CREATE INDEX IF NOT EXISTS idx_ne_countries_geom ON ne_countries USING GIST(geom);

CREATE TABLE IF NOT EXISTS ne_coastline (
    id SERIAL PRIMARY KEY,
    geom GEOMETRY(MULTILINESTRING, 4326)
);
CREATE INDEX IF NOT EXISTS idx_ne_coastline_geom ON ne_coastline USING GIST(geom);

-- Function to get detections as GeoJSON FeatureCollection
CREATE OR REPLACE FUNCTION get_detections_geojson(
    bbox_geom GEOMETRY DEFAULT NULL,
    start_time TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    end_time TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    det_class VARCHAR DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    result JSONB;
BEGIN
    SELECT jsonb_build_object(
        'type', 'FeatureCollection',
        'features', coalesce(jsonb_agg(
            jsonb_build_object(
                'type', 'Feature',
                'geometry', ST_AsGeoJSON(d.geom)::jsonb,
                'properties', jsonb_build_object(
                    'id', d.id,
                    'class', d.class,
                    'confidence', d.confidence,
                    'pass_id', d.pass_id,
                    'created_at', d.created_at,
                    'metadata', d.metadata
                )
            )
        ), '[]'::jsonb)
    )
    INTO result
    FROM detections d
    JOIN satellite_passes sp ON d.pass_id = sp.id
    WHERE (bbox_geom IS NULL OR ST_Intersects(d.geom, bbox_geom))
      AND (start_time IS NULL OR sp.acquisition_time >= start_time)
      AND (end_time IS NULL OR sp.acquisition_time <= end_time)
      AND (det_class IS NULL OR d.class = det_class);
    
    RETURN result;
END;
$$ LANGUAGE plpgsql;
