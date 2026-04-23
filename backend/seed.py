from database import db

def seed():
    with db.get_session() as session:
        # Clear existing
        session.run("MATCH (n) DETACH DELETE n")
        
        # Create some nodes
        session.run("""
        CREATE (p1:Person {name: 'John Doe', role: 'Operative'})
        CREATE (p2:Person {name: 'Jane Smith', role: 'Analyst'})
        CREATE (l1:Location {name: 'Forward Operating Base Alpha', latitude: 34.0522, longitude: -118.2437})
        CREATE (l2:Location {name: 'Central Command', latitude: 38.8951, longitude: -77.0364})
        CREATE (e1:Event {name: 'Operation Midnight', date: '2023-10-15'})
        
        CREATE (p1)-[:STATIONED_AT]->(l1)
        CREATE (p2)-[:STATIONED_AT]->(l2)
        CREATE (p1)-[:PARTICIPATED_IN]->(e1)
        CREATE (e1)-[:OCCURRED_AT]->(l1)
        """)
        print("Database seeded successfully.")

if __name__ == "__main__":
    seed()
