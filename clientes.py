import os
from dotenv import load_dotenv
from supabase import create_client
from datetime import datetime, timezone, timedelta

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key)

def ver_clientes():
    clientes = supabase.table("clientes").select("*").execute()
    print("\n--- LISTADO DE CLIENTAS ---")
    for c in clientes.data:
        print(f"{c['nombre']} - Tel: {c['telefono']}")

def clientes_en_riesgo():
    hace_30_dias = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    
    resultado = supabase.table("citas")\
        .select("cliente_id, fecha_hora, clientes(nombre, telefono)")\
        .lt("fecha_hora", hace_30_dias)\
        .eq("estado", "completada")\
        .execute()
    
    print("\n--- CLIENTAS EN RIESGO (sin visita en +30 días) ---")
    if not resultado.data:
        print("Necesitamos registrar citas primero.")
        print("Ese es el siguiente paso.")
    else:
        for r in resultado.data:
            print(f"{r['clientes']['nombre']} - Última visita: {r['fecha_hora'][:10]}")

ver_clientes()
clientes_en_riesgo()