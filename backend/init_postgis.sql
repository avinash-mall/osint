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
    metadata JSONB DEFAULT '{}',
    source_hash VARCHAR(64),
    source_filename VARCHAR(255),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create spatial index on footprint
CREATE INDEX IF NOT EXISTS idx_passes_footprint ON satellite_passes USING GIST(footprint);
CREATE INDEX IF NOT EXISTS idx_passes_time ON satellite_passes(acquisition_time);
CREATE INDEX IF NOT EXISTS idx_passes_source_time ON satellite_passes(source_hash, acquisition_time);

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

CREATE TABLE IF NOT EXISTS detection_target_candidates (
    id SERIAL PRIMARY KEY,
    detection_id INTEGER REFERENCES detections(id) ON DELETE CASCADE,
    target_id VARCHAR(255) NOT NULL,
    target_name VARCHAR(255),
    score REAL DEFAULT 0,
    reason TEXT,
    status VARCHAR(50) DEFAULT 'pending',
    evidence JSONB DEFAULT '{}',
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (detection_id, target_id)
);
CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_detection ON detection_target_candidates(detection_id, status);
CREATE INDEX IF NOT EXISTS idx_detection_target_candidates_target ON detection_target_candidates(target_id, status);

CREATE TABLE IF NOT EXISTS ontology_updates (
    id SERIAL PRIMARY KEY,
    source_type VARCHAR(80) NOT NULL,
    source_id VARCHAR(255),
    domain VARCHAR(50) DEFAULT 'OSINT',
    status VARCHAR(50) DEFAULT 'pending_review',
    summary TEXT,
    proposed_entities JSONB DEFAULT '[]',
    proposed_relationships JSONB DEFAULT '[]',
    context JSONB DEFAULT '{}',
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ontology_updates_source ON ontology_updates(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_ontology_updates_status ON ontology_updates(status);

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

-- Streaming feed connectors and normalized live events
CREATE TABLE IF NOT EXISTS feed_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    feed_type VARCHAR(100) NOT NULL,
    protocol VARCHAR(50) NOT NULL,
    endpoint VARCHAR(1024) NOT NULL,
    topic VARCHAR(255) DEFAULT 'feeds',
    parser VARCHAR(100),
    enabled BOOLEAN DEFAULT TRUE,
    status VARCHAR(50) DEFAULT 'configured',
    last_error TEXT,
    last_seen TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feed_sources_type ON feed_sources(feed_type);

CREATE TABLE IF NOT EXISTS feed_events (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES feed_sources(id) ON DELETE CASCADE,
    event_type VARCHAR(100),
    payload JSONB DEFAULT '{}',
    geom GEOMETRY(POINT, 4326),
    observed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feed_events_geom ON feed_events USING GIST(geom);

-- Minimal offline fallback basemap. Real Natural Earth imports can replace these
-- rows, but Martin will not start with empty advertised layers.
INSERT INTO ne_countries (name, admin, iso_a3, pop_est, gdp_md_est, geom)
SELECT * FROM (
    VALUES
    ('United Arab Emirates', 'United Arab Emirates', 'ARE', 9890000, 421000,
     ST_Multi(ST_GeomFromText('POLYGON((51.5 22.6, 51.5 26.5, 56.6 26.5, 56.6 22.6, 51.5 22.6))', 4326))),
    ('Oman', 'Oman', 'OMN', 4640000, 88000,
     ST_Multi(ST_GeomFromText('POLYGON((52.0 16.5, 52.0 26.5, 60.0 26.5, 60.0 16.5, 52.0 16.5))', 4326))),
    ('Saudi Arabia', 'Saudi Arabia', 'SAU', 36000000, 1108000,
     ST_Multi(ST_GeomFromText('POLYGON((34.5 16.0, 34.5 32.5, 55.7 32.5, 55.7 16.0, 34.5 16.0))', 4326)))
) AS seed(name, admin, iso_a3, pop_est, gdp_md_est, geom)
WHERE NOT EXISTS (SELECT 1 FROM ne_countries);

INSERT INTO ne_coastline (geom)
SELECT ST_Multi(ST_GeomFromText('LINESTRING(34.5 16.0, 42.0 15.0, 48.0 25.0, 56.6 26.5, 60.0 22.0)', 4326))
WHERE NOT EXISTS (SELECT 1 FROM ne_coastline);

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

-- Detection tracking tables (satellite-pass temporal association)
CREATE TABLE IF NOT EXISTS detection_tracks (
    id              SERIAL PRIMARY KEY,
    track_uid       VARCHAR(64) UNIQUE NOT NULL,
    primary_class   VARCHAR(100) NOT NULL,
    category        VARCHAR(50),
    threat_level    VARCHAR(20),
    status          VARCHAR(20) DEFAULT 'tentative',
    pinned          BOOLEAN DEFAULT FALSE,
    obs_count       INTEGER DEFAULT 0,
    miss_count      INTEGER DEFAULT 0,
    first_seen      TIMESTAMP WITH TIME ZONE,
    last_seen       TIMESTAMP WITH TIME ZONE,
    last_centroid   GEOMETRY(POINT, 4326),
    last_velocity   JSONB DEFAULT '{}',
    path            GEOMETRY(LINESTRING, 4326),
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_detection_tracks_status        ON detection_tracks(status);
CREATE INDEX IF NOT EXISTS idx_detection_tracks_last_seen     ON detection_tracks(last_seen);
CREATE INDEX IF NOT EXISTS idx_detection_tracks_last_centroid ON detection_tracks USING GIST(last_centroid);
CREATE INDEX IF NOT EXISTS idx_detection_tracks_path          ON detection_tracks USING GIST(path);

CREATE TABLE IF NOT EXISTS detection_track_members (
    id            SERIAL PRIMARY KEY,
    track_id      INTEGER REFERENCES detection_tracks(id) ON DELETE CASCADE,
    detection_id  INTEGER REFERENCES detections(id) ON DELETE CASCADE UNIQUE,
    pass_id       INTEGER REFERENCES satellite_passes(id) ON DELETE CASCADE,
    observed_at   TIMESTAMP WITH TIME ZONE,
    centroid      GEOMETRY(POINT, 4326),
    seq_index     INTEGER,
    cost          REAL,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dtm_track     ON detection_track_members(track_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_dtm_detection ON detection_track_members(detection_id);
CREATE INDEX IF NOT EXISTS idx_dtm_pass      ON detection_track_members(pass_id);
