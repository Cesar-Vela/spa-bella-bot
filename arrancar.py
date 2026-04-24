import os
from dotenv import load_dotenv
import anthropic

from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

load_dotenv()
claude = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

def consultar_claude(mensaje):
    respuesta = claude.messages.create(
     model="claude-sonnet-4-20250514",
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


@app.route("/bot", methods=["POST"])
def bot():
    mensaje = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    print(f"\nMENSAJE de {remitente}: {mensaje}")

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
    app.run(port=5000, debug=True)