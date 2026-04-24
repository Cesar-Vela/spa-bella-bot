from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

@app.route("/bot", methods=["POST"])
def bot():
    mensaje_recibido = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    
    print(f"Mensaje de {remitente}: {mensaje_recibido}")
    
    respuesta = MessagingResponse()
    respuesta.message(f"Hola! Recibí tu mensaje: '{mensaje_recibido}'. El bot del Spa Bella está funcionando.")
    
    return str(respuesta)

if __name__ == "__main__":
    app.run(debug=True, port=5000)