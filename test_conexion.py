import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key)

servicios = supabase.table("servicios").select("*").execute()

for s in servicios.data:
    print(f"{s['nombre']} - ${s['precio']:,}")