import os
import pyTigerGraph as tg
from dotenv import load_dotenv

load_dotenv()

# The Database Secret is currently stored in the TG_PASSWORD environment variable
tg_secret = os.environ.get("TG_PASSWORD")
tg_host = os.environ.get("TG_HOST")

# Initialize connection without username/password
conn = tg.TigerGraphConnection(host=tg_host)

# Generate and set the auth token using the secret
print("Generating auth token...")
conn.getToken(secret=tg_secret)

with open("schema/schema.gsql", "r") as f:
    gsql_script = f.read()

print("Deploying schema via Token Authentication...")
print(conn.gsql(gsql_script))
