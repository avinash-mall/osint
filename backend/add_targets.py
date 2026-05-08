from database import db

def add_targets():
    with db.get_session() as session:
        session.run("""
        MERGE (t1:Target {name: 'Operation Sandstorm Leader'})
        SET t1.priority = 'High', t1.status = 'Active', t1.description = 'Suspected command and control node.',
            t1.latitude = 25.241, t1.longitude = 55.308
        MERGE (t2:Target {name: 'Supply Convoy Echo'})
        SET t2.priority = 'Medium', t2.status = 'Monitored', t2.description = 'Logistics resupply group.',
            t2.latitude = 24.961, t2.longitude = 55.112
        MERGE (t3:Target {name: 'Radio Tower Delta'})
        SET t3.priority = 'Low', t3.status = 'Eliminated', t3.description = 'Disabled comms relay.',
            t3.latitude = 25.383, t3.longitude = 55.419
        MERGE (t4:Target {name: 'Courier X'})
        SET t4.priority = 'High', t4.status = 'Active', t4.description = 'VIP transport tracking.',
            t4.latitude = 25.078, t4.longitude = 55.179
        """)
        print("Targets added.")

if __name__ == "__main__":
    add_targets()
