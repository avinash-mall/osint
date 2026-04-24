import random
import time
from datetime import datetime, timedelta
from database import db
import os

def generate_tactical_data(session):
    print("Clearing existing database...")
    session.run("MATCH (n) DETACH DELETE n")

    print("Generating base entities...")
    # Base entities: Regions, LaunchPoints, Bases
    session.run("""
    CREATE (b1:Base {name: 'CENTCOM Main', latitude: 25.2, longitude: 55.2})
    CREATE (lp1:LaunchPoint {name: 'LaunchPoint Tango', latitude: 25.5, longitude: 55.1, threatRadius: 100000})
    CREATE (lp2:LaunchPoint {name: 'LaunchPoint X-Ray', latitude: 25.1, longitude: 55.4, threatRadius: 150000})
    CREATE (tg1:Target {name: 'Target Alpha', priority: 'High', status: 'Active'})
    """)

    # Generate Vessels and Aircraft
    print("Generating Tracks and Observations...")
    now = datetime.utcnow()
    
    for i in range(20):
        asset_id = f"Vessel-{i}"
        asset_type = random.choice(["Vessel", "Aircraft"])
        speed = random.uniform(20, 500) if asset_type == "Aircraft" else random.uniform(5, 30)
        
        session.run(f"CREATE (a:Asset:{asset_type} {{id: '{asset_id}', callsign: 'Callsign-{i}', speed: {speed}}})")

        # Generate a track history for each asset
        lat = random.uniform(24.0, 26.0)
        lon = random.uniform(54.0, 56.0)
        
        for min_offset in range(60): # last 60 minutes
            obs_time = now - timedelta(minutes=60 - min_offset)
            # simulate movement
            lat += random.uniform(-0.01, 0.01)
            lon += random.uniform(-0.01, 0.01)
            
            session.run("""
            MATCH (a:Asset {id: $asset_id})
            CREATE (o:Observation {
                timestamp: $timestamp, 
                isoTime: $isoTime,
                latitude: $lat, 
                longitude: $lon,
                heading: $heading
            })
            CREATE (a)-[:OBSERVED_AT]->(o)
            """, {
                "asset_id": asset_id,
                "timestamp": int(obs_time.timestamp()),
                "isoTime": obs_time.isoformat(),
                "lat": lat,
                "lon": lon,
                "heading": random.uniform(0, 360)
            })
            
            # create communication events
            if random.random() < 0.05:
                target_i = random.randint(0, 19)
                if target_i != i:
                    session.run("""
                    MATCH (a1:Asset {id: $id1}), (a2:Asset {id: $id2})
                    CREATE (a1)-[:COMMUNICATED_WITH {time: $time}]->(a2)
                    """, {"id1": asset_id, "id2": f"Vessel-{target_i}", "time": obs_time.isoformat()})

    # Generate Satellite Constellation
    print("Generating Satellite Constellation...")
    satellites = [
        {"name": "Sentinel-2A", "type": "Optical", "lat": 25.0, "lon": 55.0, "orbit_alt": 786000, "status": "Active"},
        {"name": "Landsat-9", "type": "Optical", "lat": 24.5, "lon": 54.5, "orbit_alt": 705000, "status": "Active"},
        {"name": "WorldView-3", "type": "Optical", "lat": 25.5, "lon": 55.5, "orbit_alt": 617000, "status": "Active"},
        {"name": "SAR-Lupe 1", "type": "Radar", "lat": 24.0, "lon": 56.0, "orbit_alt": 500000, "status": "Active"},
        {"name": "NOAA-20", "type": "Optical", "lat": 26.0, "lon": 54.0, "orbit_alt": 824000, "status": "Standby"},
        {"name": "GOES-16", "type": "Optical", "lat": 0.0, "lon": -75.0, "orbit_alt": 35786000, "status": "Active"},
    ]
    
    for sat in satellites:
        session.run("""
        CREATE (s:Satellite {
            name: $name,
            type: $type,
            lat: $lat,
            lon: $lon,
            orbit_alt: $orbit_alt,
            status: $status
        })
        """, sat)

def seed():
    with db.get_session() as session:
        generate_tactical_data(session)
        print("Database seeded successfully with tactical dataset.")

if __name__ == "__main__":
    seed()
