"""
Real-world demo dataset: Customer contacts to import.
Source A: 20 unique customers
Source B: Same 20 customers + 5 duplicates mixed in (simulates re-importing)
"""

CUSTOMERS_SOURCE_A = [
    {"id": "CUST-001", "name": "Alice Johnson", "email": "alice@example.com", "phone": "+1-555-0101", "company": "Acme Corp"},
    {"id": "CUST-002", "name": "Bob Smith", "email": "bob@example.com", "phone": "+1-555-0102", "company": "Globex"},
    {"id": "CUST-003", "name": "Carol White", "email": "carol@example.com", "phone": "+1-555-0103", "company": "Initech"},
    {"id": "CUST-004", "name": "David Lee", "email": "david@example.com", "phone": "+1-555-0104", "company": "Umbrella Corp"},
    {"id": "CUST-005", "name": "Eve Martinez", "email": "eve@example.com", "phone": "+1-555-0105", "company": "Stark Industries"},
    {"id": "CUST-006", "name": "Frank Chen", "email": "frank@example.com", "phone": "+1-555-0106", "company": "Wayne Enterprises"},
    {"id": "CUST-007", "name": "Grace Kim", "email": "grace@example.com", "phone": "+1-555-0107", "company": "Cyberdyne"},
    {"id": "CUST-008", "name": "Henry Patel", "email": "henry@example.com", "phone": "+1-555-0108", "company": "Oscorp"},
    {"id": "CUST-009", "name": "Ivy Brown", "email": "ivy@example.com", "phone": "+1-555-0109", "company": "Massive Dynamic"},
    {"id": "CUST-010", "name": "Jack Wilson", "email": "jack@example.com", "phone": "+1-555-0110", "company": "Aperture Science"},
    {"id": "CUST-011", "name": "Kate Davis", "email": "kate@example.com", "phone": "+1-555-0111", "company": "Hooli"},
    {"id": "CUST-012", "name": "Liam Garcia", "email": "liam@example.com", "phone": "+1-555-0112", "company": "Pied Piper"},
    {"id": "CUST-013", "name": "Mia Thompson", "email": "mia@example.com", "phone": "+1-555-0113", "company": "Soylent Corp"},
    {"id": "CUST-014", "name": "Noah Anderson", "email": "noah@example.com", "phone": "+1-555-0114", "company": "Wonka Industries"},
    {"id": "CUST-015", "name": "Olivia Taylor", "email": "olivia@example.com", "phone": "+1-555-0115", "company": "Dunder Mifflin"},
    {"id": "CUST-016", "name": "Paul Thomas", "email": "paul@example.com", "phone": "+1-555-0116", "company": "Sterling Cooper"},
    {"id": "CUST-017", "name": "Quinn Jackson", "email": "quinn@example.com", "phone": "+1-555-0117", "company": "Prestige Worldwide"},
    {"id": "CUST-018", "name": "Rachel Harris", "email": "rachel@example.com", "phone": "+1-555-0118", "company": "Vandelay Industries"},
    {"id": "CUST-019", "name": "Sam Clark", "email": "sam@example.com", "phone": "+1-555-0119", "company": "Bluth Company"},
    {"id": "CUST-020", "name": "Tina Lewis", "email": "tina@example.com", "phone": "+1-555-0120", "company": "Sirius Cybernetics"},
]

# Source B: Same customers + 5 duplicates mixed in
# This simulates a real-world scenario where you re-import a CSV that has some repeated rows
CUSTOMERS_SOURCE_B = [
    {"id": "CUST-001", "name": "Alice Johnson", "email": "alice@example.com", "phone": "+1-555-0101", "company": "Acme Corp"},
    {"id": "CUST-002", "name": "Bob Smith", "email": "bob@example.com", "phone": "+1-555-0102", "company": "Globex"},
    {"id": "CUST-001", "name": "Alice Johnson", "email": "alice@example.com", "phone": "+1-555-0101", "company": "Acme Corp"},  # DUPLICATE
    {"id": "CUST-003", "name": "Carol White", "email": "carol@example.com", "phone": "+1-555-0103", "company": "Initech"},
    {"id": "CUST-004", "name": "David Lee", "email": "david@example.com", "phone": "+1-555-0104", "company": "Umbrella Corp"},
    {"id": "CUST-005", "name": "Eve Martinez", "email": "eve@example.com", "phone": "+1-555-0105", "company": "Stark Industries"},
    {"id": "CUST-002", "name": "Bob Smith", "email": "bob@example.com", "phone": "+1-555-0102", "company": "Globex"},  # DUPLICATE
    {"id": "CUST-006", "name": "Frank Chen", "email": "frank@example.com", "phone": "+1-555-0106", "company": "Wayne Enterprises"},
    {"id": "CUST-007", "name": "Grace Kim", "email": "grace@example.com", "phone": "+1-555-0107", "company": "Cyberdyne"},
    {"id": "CUST-008", "name": "Henry Patel", "email": "henry@example.com", "phone": "+1-555-0108", "company": "Oscorp"},
    {"id": "CUST-009", "name": "Ivy Brown", "email": "ivy@example.com", "phone": "+1-555-0109", "company": "Massive Dynamic"},
    {"id": "CUST-010", "name": "Jack Wilson", "email": "jack@example.com", "phone": "+1-555-0110", "company": "Aperture Science"},
    {"id": "CUST-011", "name": "Kate Davis", "email": "kate@example.com", "phone": "+1-555-0111", "company": "Hooli"},
    {"id": "CUST-003", "name": "Carol White", "email": "carol@example.com", "phone": "+1-555-0103", "company": "Initech"},  # DUPLICATE
    {"id": "CUST-012", "name": "Liam Garcia", "email": "liam@example.com", "phone": "+1-555-0112", "company": "Pied Piper"},
    {"id": "CUST-013", "name": "Mia Thompson", "email": "mia@example.com", "phone": "+1-555-0113", "company": "Soylent Corp"},
    {"id": "CUST-014", "name": "Noah Anderson", "email": "noah@example.com", "phone": "+1-555-0114", "company": "Wonka Industries"},
    {"id": "CUST-015", "name": "Olivia Taylor", "email": "olivia@example.com", "phone": "+1-555-0115", "company": "Dunder Mifflin"},
    {"id": "CUST-004", "name": "David Lee", "email": "david@example.com", "phone": "+1-555-0104", "company": "Umbrella Corp"},  # DUPLICATE
    {"id": "CUST-016", "name": "Paul Thomas", "email": "paul@example.com", "phone": "+1-555-0116", "company": "Sterling Cooper"},
    {"id": "CUST-017", "name": "Quinn Jackson", "email": "quinn@example.com", "phone": "+1-555-0117", "company": "Prestige Worldwide"},
    {"id": "CUST-018", "name": "Rachel Harris", "email": "rachel@example.com", "phone": "+1-555-0118", "company": "Vandelay Industries"},
    {"id": "CUST-019", "name": "Sam Clark", "email": "sam@example.com", "phone": "+1-555-0119", "company": "Bluth Company"},
    {"id": "CUST-005", "name": "Eve Martinez", "email": "eve@example.com", "phone": "+1-555-0105", "company": "Stark Industries"},  # DUPLICATE
    {"id": "CUST-020", "name": "Tina Lewis", "email": "tina@example.com", "phone": "+1-555-0120", "company": "Sirius Cybernetics"},
]
