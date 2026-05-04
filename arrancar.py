import os
from dotenv import load_dotenv
import anthropic
from supabase import create_client

from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

load_dotenv()
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key)

claude = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))
user_states = {}
def consultar_claude(mensaje):
    respuesta = claude.messages.create(
     model="claude-sonnet-4-5",
        max_tokens=300,
        temperature=0.3,
        messages=[
            {
                "role": "user",
                "content": f"""
Eres la asistente virtual del Spa Bella.
Responde en español, de forma amable, breve y profesional.
Ayuda con servicios, precios y citas.
Mensaje del cliente: {mensaje}
"""
            }
        ]
    )
    return respuesta.content[0].text.strip()
def buscar_servicio(mensaje):
    data = supabase.table("servicios").select("*").execute()
    mensaje_lower = mensaje.lower()

    for servicio in data.data:
        nombre = servicio["nombre"].lower()

        if nombre in mensaje_lower:
            return servicio

    return None


@app.route("/bot", methods=["POST"])
def bot():
    mensaje = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    print(f"\nMENSAJE de {remitente}: {mensaje}")

    mensaje_lower = mensaje.lower()

    if remitente not in user_states:
        user_states[remitente] = {
            "intent": None,
            "step": "inicio",
            "servicio": None
        }

    state = user_states[remitente]
    servicio = buscar_servicio(mensaje)

    if mensaje_lower in ["hola", "buenas", "buenos dias", "buenos días", "buenas tardes", "buenas noches"]:
        state["intent"] = None
        state["step"] = "inicio"
        state["servicio"] = None

        respuesta_texto = """¡Hola! Bienvenido/a al Spa Bella 🌸

Soy tu asistente virtual y estoy aquí para ayudarte. Puedo brindarte información sobre:

· Nuestros servicios de spa y tratamientos
· Precios y promociones
· Agendar citas
· Horarios de atención

¿En qué puedo asistirte hoy?"""

    elif "promocion" in mensaje_lower or "promociones" in mensaje_lower or "promo" in mensaje_lower:
        state["intent"] = "promociones"
        state["step"] = "inicio"

        respuesta_texto = """✨ Claro 😊

En este momento puedo ayudarte con promociones en servicios seleccionados como faciales, masajes y tratamientos corporales.

Para darte una promo adecuada, dime qué te interesa más:
facial, masaje, depilación o tratamiento corporal."""

    elif "servicio" in mensaje_lower or "servicios" in mensaje_lower:
        data = supabase.table("servicios").select("*").execute()

        lista = ""
        for s in data.data:
            lista += f"{s['nombre']} - ${s['precio']}\n"

        respuesta_texto = f"Claro ✨ estos son nuestros servicios disponibles en Spa Bella:\n\n{lista}\n¿Te gustaría agendar alguno? 😊"

    elif "precio" in mensaje_lower or "valor" in mensaje_lower or "cuánto" in mensaje_lower:
        state["intent"] = "precio"
        state["step"] = "esperando_servicio"

        if servicio:
            state["servicio"] = servicio["nombre"]
            state["step"] = "precio_entregado"
            respuesta_texto = f"✨ El {servicio['nombre']} tiene un valor de ${servicio['precio']}\n\n💆‍♀️ Ideal para relajarte y cuidarte.\n\n¿Te gustaría agendar tu cita? 😊"
        else:
            respuesta_texto = "Claro 😊 ¿sobre qué servicio te gustaría saber el precio? Puedes decirme, por ejemplo: masaje relajante o facial hidratante."

    elif state["intent"] == "precio" and servicio:
        state["servicio"] = servicio["nombre"]
        state["step"] = "precio_entregado"
        respuesta_texto = f"✨ El {servicio['nombre']} tiene un valor de ${servicio['precio']}\n\n💆‍♀️ Ideal para relajarte y cuidarte.\n\n¿Te gustaría agendar tu cita? 😊"

    elif state["intent"] == "precio" and not servicio:
        respuesta_texto = "Perfecto 😊 para darte el precio exacto necesito el nombre del servicio.\n\nPuedes escribir: masaje relajante, facial hidratante o depilación axilas."

    elif "cita" in mensaje_lower or "agendar" in mensaje_lower:
        state["intent"] = "agendar"
        state["step"] = "esperando_servicio"
        respuesta_texto = "Perfecto ✨ ¿para qué servicio deseas agendar tu cita?"

    else:
        try:
            respuesta_texto = consultar_claude(mensaje)
            print(f"CLAUDE RESPONDIO: {respuesta_texto}")
        except Exception as e:
            print(f"ERROR EN CLAUDE: {e}")
            respuesta_texto = "Hola, soy el asistente del Spa Bella. ¿En qué te puedo ayudar?"

    respuesta = MessagingResponse()
    respuesta.message(respuesta_texto)
    print(f"ENVIANDO A WHATSAPP: {str(respuesta)}")
    return Response(str(respuesta), mimetype="application/xml")
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))