from database import db
import random
from datetime import datetime, timedelta

def add_constellation():
    with db.get_session() as session:
        print("Adding Constellation data...")
        
        # Add a few satellites
        satellites = [
            {"id": "SAT-101", "name": "Observer-Alpha", "type": "Optical", "orbit_alt": 400},
            {"id": "SAT-102", "name": "SIGINT-Beta", "type": "RF/SIGINT", "orbit_alt": 600},
            {"id": "SAT-103", "name": "SAR-Gamma", "type": "Radar", "orbit_alt": 500},
            {"id": "SAT-104", "name": "CommRelay-Delta", "type": "Comms", "orbit_alt": 800},
        ]
        
        for sat in satellites:
            session.run("""
            CREATE (s:Satellite {
                id: $id, 
                name: $name, 
                type: $type, 
                orbit_alt: $orbit_alt, 
                lat: $lat, 
                lon: $lon,
                status: 'Nominal'
            })
            """, {**sat, "lat": random.uniform(-90, 90), "lon": random.uniform(-180, 180)})
            
        print("Satellites added.")

if __name__ == "__main__":
    add_constellation()
