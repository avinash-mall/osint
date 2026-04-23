from database import db

def add_targets():
    with db.get_session() as session:
        session.run("""
        CREATE (t1:Target {name: 'Operation Sandstorm Leader', priority: 'High', status: 'Active', description: 'Suspected command and control node.'})
        CREATE (t2:Target {name: 'Supply Convoy Echo', priority: 'Medium', status: 'Monitored', description: 'Logistics resupply group.'})
        CREATE (t3:Target {name: 'Radio Tower Delta', priority: 'Low', status: 'Eliminated', description: 'Disabled comms relay.'})
        CREATE (t4:Target {name: 'Courier X', priority: 'High', status: 'Active', description: 'VIP transport tracking.'})
        """)
        print("Targets added.")

if __name__ == "__main__":
    add_targets()
