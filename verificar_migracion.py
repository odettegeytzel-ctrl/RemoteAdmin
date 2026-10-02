import sqlite3

c = sqlite3.connect("remoteadmin.db")

print("=== VERIFICACIÓN ===")

print("devices:", c.execute("SELECT COUNT(*) FROM devices").fetchone()[0])
print("installed_software:", c.execute("SELECT COUNT(*) FROM installed_software").fetchone()[0])
print("alerts:", c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0])
print("recordings:", c.execute("SELECT COUNT(*) FROM recordings").fetchone()[0])

row = c.execute("""
    SELECT device_id, agent_token_active, agent_token_hash, agent_token_issued_at
    FROM devices
""").fetchone()

print("device_id:", row[0][:4] + "..." + row[0][-4:])
print("token activo:", bool(row[1]))
print("hash del token guardado:", bool(row[2]))
print("fecha de emisión:", bool(row[3]))

c.close()
