import os
from dotenv import load_dotenv
from pyngrok import ngrok

# Cargar variables de entorno
load_dotenv()

# Configurar el authtoken de ngrok
ngrok.set_auth_token(os.getenv("NGROK_TOKEN"))

# Crear el túnel hacia el puerto 5000
tunnel = ngrok.connect(5000)

# Mostrar la URL pública
print("URL pública:", tunnel.public_url)

# Mantener el túnel activo
try:
    print("Ngrok está en ejecución. Presiona Ctrl + C para detenerlo.")
    while True:
        pass
except KeyboardInterrupt:
    print("\nCerrando ngrok...")
    ngrok.disconnect(tunnel.public_url)
    ngrok.kill()