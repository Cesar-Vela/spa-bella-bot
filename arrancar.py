import os
import json
import unicodedata
from datetime import datetime, timedelta
from dotenv import load_dotenv
import anthropic
from supabase import create_client
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)
load_dotenv()

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
claude = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

# ═══════════════════════════════════════════════════════════════
# HISTORIAL — única memoria que necesitamos
# ═══════════════════════════════════════════════════════════════
user_history = {}

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE HORARIOS
# ═══════════════════════════════════════════════════════════════
HORARIOS_DISPONIBLES = {
    "lunes":     ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"],
    "martes":    ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"],
    "miercoles": ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"],
    "jueves":    ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00"],
    "viernes":   ["09:00", "10:00", "11:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00"],
    "sabado":    ["09:00", "10:00", "11:00", "12:00", "13:00"],
}

DIAS_ES = {
    "monday": "lunes", "tuesday": "martes", "wednesday": "miercoles",
    "thursday": "jueves", "friday": "viernes", "saturday": "sabado", "sunday": "domingo"
}

# ═══════════════════════════════════════════════════════════════
# FRASES ALUSIVAS — le dan calidez humana a cada servicio
# Valentina las usa al mostrar el menú numerado
# ═══════════════════════════════════════════════════════════════
FRASES_SERVICIO = {
    # — Faciales —
    "limpieza facial basica":     "El reset perfecto para tu piel — limpia, fresca y sin impurezas 🌿",
    "facial hidratante":          "Tu piel sale radiante y llena de luz ✨",
    # — Masajes —
    "masaje relajante":           "Para soltar todo el estrés acumulado y desconectarte del mundo 💆‍♀️",
    "masaje descontracturante":   "Ideal si traes la espalda cargada o con nudos — libera de verdad 💪",
    # — Depilación —
    "depilacion piernas completas":"Piernas súper suaves de tobillo a muslo, para que las luzcas sin pena 🦵",
    "depilacion axilas":          "Rápido, limpio y sin irritación — listo en 20 minutos 👌",
    # — Uñas —
    "manicure clasico":           "Uñas arregladas y presentables — clásico que nunca falla 💅",
    "manicure semipermanente":    "Dura hasta 3 semanas intacto — lo favorito de nuestras clientas 💅",
    # — Corporales —
    "tratamiento reductivo":      "Para trabajar esas zonas que te preocupan con técnica y dedicación 🎯",
    # — Pestañas —
    "diseno de pestanas":         "Una mirada que habla por sí sola — lifting, tinte y cejas incluidos 👁️",
    # — Cabello —
    "tinte + corte":              "Cambio de look completo — color y corte que te van a encantar 🌟",
    "keratina":                   "Adiós frizz, hola cabello brillante y manejable por semanas ✨",
}

DESCRIPCIONES_SERVICIO = {
    # — Faciales —
    "limpieza facial basica":     "Limpieza profunda que elimina impurezas, puntos negros y exceso de grasa. La piel queda fresca y lista para recibir tratamientos.",
    "facial hidratante":          "Limpieza profunda, exfoliación suave e hidratación intensiva. Sales con la piel radiante y llena de luz.",
    # — Masajes —
    "masaje relajante":           "Movimientos suaves con aromaterapia para bajar el estrés y soltar la tensión acumulada. El cuerpo sale completamente en paz.",
    "masaje descontracturante":   "Presión media-alta enfocada en liberar contracturas profundas. Perfecto cuando sientes la espalda dura o con nudos.",
    # — Depilación —
    "depilacion piernas completas":"Depilación completa de tobillo a muslo con cera. La piel queda suave y libre de vello por semanas.",
    "depilacion axilas":          "Cera de alta calidad que deja la zona limpia y suave sin irritar. Listo en 20 minutos.",
    # — Uñas —
    "manicure clasico":           "Limpieza de cutícula, forma y esmaltado tradicional. Manos presentables y cuidadas.",
    "manicure semipermanente":    "Esmaltado que dura hasta 3 semanas sin descascararse ni perder brillo. Ideal si no tienes tiempo para retocar.",
    # — Corporales —
    "tratamiento reductivo":      "Trabajamos zonas localizadas como abdomen, cintura o piernas con técnicas manuales y productos reductores. Complementa tus hábitos saludables.",
    # — Pestañas —
    "diseno de pestanas":         "Incluye lifting de pestañas, tinte y arreglo de cejas. La mirada queda enmarcada y espectacular.",
    # — Cabello —
    "tinte + corte":              "Color personalizado según tu tono de piel y corte que favorece tu tipo de rostro. Renovación completa.",
    "keratina":                   "Tratamiento capilar que controla el frizz y suaviza el cabello por semanas. Sale con brillo y completamente manejable.",
}

# ═══════════════════════════════════════════════════════════════
# PERSONALIDAD DE VALENTINA
# ═══════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """
Eres Valentina, recepcionista y asesora de bienestar de Spa Bella.
Atiendes por WhatsApp como una persona real — cálida, cercana, con experiencia en spa.

ESTILO:
- Conversacional y natural. Como si estuvieras en la recepción del spa.
- Máximo 4 líneas salvo cuando muestres menús numerados de servicios.
- Emojis con moderación: 🌸 💆‍♀️ ✨ 😊 (máximo 2 por mensaje).
- NUNCA digas que eres IA, bot o asistente virtual. Eres Valentina.
- Siempre termina con una invitación a la acción.

REGLAS:
- NUNCA inventes precios. Usa solo los que vienen de la herramienta get_servicios.
- Si preguntan algo fuera del spa (política, noticias, etc.), redirige amablemente.
- NO ofreces: botox, ácido hialurónico, cirugías, láser médico.
- Dirección: Av. Principal 456, Col. Centro, frente al parque 📍
- Horario: Lun-Jue 9am-6pm, Vie 9am-8pm, Sáb 9am-4pm. Domingo cerrado.

BIENVENIDA — PRIMER MENSAJE:
Cuando el cliente saluda por primera vez, preséntate y muestra SIEMPRE el menú de categorías:

"¡Hola! 🌸 Bienvenida a Spa Bella, soy Valentina.
Estoy aquí para consentirte. ¿Qué te gustaría explorar hoy?

1️⃣ Masajes — relajante, descontracturante
2️⃣ Faciales — hidratación, anti-edad, limpieza
3️⃣ Depilación — piernas, axilas
4️⃣ Uñas — manicure clásico y semipermanente
5️⃣ Pestañas — diseño y lifting
6️⃣ Cabello — keratina, tinte y corte
7️⃣ Tratamientos corporales — reductivos

Responde con el número o cuéntame qué buscas 😊"

MENÚS NUMERADOS DE SERVICIOS — MUY IMPORTANTE:
Cuando muestres servicios con precios, SIEMPRE usa este formato exacto:
1. *Nombre del servicio* — $precio
   _frase alusiva_
2. *Nombre del servicio* — $precio
   _frase alusiva_
...
Responde con el número que más te guste 😊

Las frases alusivas las recibirás junto con los datos de cada servicio.
Esto es OBLIGATORIO — hace que Valentina suene humana, no como un bot.

SELECCIÓN POR NÚMERO:
Si el cliente responde con un número (1, 2, 3...) después de ver un menú de SERVICIOS,
usa la herramienta get_servicios con la categoría que estabas mostrando para confirmar cuál eligió.
Si el número corresponde al menú de CATEGORÍAS (1-7), muestra los servicios de esa categoría.

POST-CITA CONFIRMADA:
Cuando una cita queda confirmada, SIEMPRE ofrece agendar otro servicio así:
"¿Te gustaría aprovechar y agendar otro servicio para ese mismo día o en otra fecha? 
Tenemos faciales, depilación, uñas y más 😊"
Si el cliente dice que sí, inicia el flujo de agendamiento desde cero para el nuevo servicio.

OBJETIVO: Entender qué busca el cliente, recomendarle lo mejor, y llevarlo a agendar.
Nunca dejes la conversación sin una invitación a continuar.
"""

# ═══════════════════════════════════════════════════════════════
# HERRAMIENTAS (tools) que Claude puede llamar
# ═══════════════════════════════════════════════════════════════
TOOLS = [
    {
        "name": "get_servicios",
        "description": (
            "Obtiene la lista de servicios del spa desde la base de datos. "
            "Úsala cuando el cliente pregunta por servicios, precios, categorías, "
            "o cuando necesitas confirmar qué servicio eligió por número. "
            "Puedes filtrar por categoría o traer todos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "categoria": {
                    "type": "string",
                    "description": (
                        "Categoría a filtrar: masajes, faciales, depilacion, "
                        "corporales, unas, pestanas, cabello. "
                        "Si se omite, devuelve todos los servicios."
                    )
                }
            },
            "required": []
        }
    },
    {
        "name": "get_horarios",
        "description": (
            "Devuelve los horarios disponibles para una fecha específica. "
            "Úsala cuando el cliente quiere agendar y dice un día o fecha."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_texto": {
                    "type": "string",
                    "description": (
                        "El día o fecha que mencionó el cliente. "
                        "Ejemplos: 'mañana', 'viernes', 'sábado', '2025-05-20'."
                    )
                }
            },
            "required": ["fecha_texto"]
        }
    },
    {
        "name": "guardar_cita",
        "description": (
            "Guarda una cita confirmada en la base de datos. "
            "Úsala SOLO cuando ya tienes: nombre del cliente, servicio elegido, "
            "fecha y hora confirmados."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_cliente": {
                    "type": "string",
                    "description": "Nombre completo del cliente."
                },
                "telefono": {
                    "type": "string",
                    "description": "Número de teléfono del cliente (sin 'whatsapp:')."
                },
                "nombre_servicio": {
                    "type": "string",
                    "description": "Nombre exacto del servicio tal como aparece en la base de datos."
                },
                "fecha": {
                    "type": "string",
                    "description": "Fecha en formato YYYY-MM-DD."
                },
                "hora": {
                    "type": "string",
                    "description": "Hora en formato HH:MM (ejemplo: 10:00, 14:00)."
                }
            },
            "required": ["nombre_cliente", "telefono", "nombre_servicio", "fecha", "hora"]
        }
    }
]

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def normalizar(texto):
    texto = (texto or "").lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def formatear_precio(valor):
    try:
        return f"${int(valor):,}".replace(",", ".")
    except Exception:
        return f"${valor}"


def frase_alusiva(nombre_servicio):
    return FRASES_SERVICIO.get(normalizar(nombre_servicio), "Una de nuestras opciones más solicitadas 😊")


def descripcion_servicio(nombre_servicio):
    return DESCRIPCIONES_SERVICIO.get(normalizar(nombre_servicio), "Un servicio pensado para que salgas sintiéndote increíble.")


def servicio_pertenece_categoria(servicio, categoria):
    n = normalizar(servicio.get("nombre", ""))
    mapa = {
        "masajes":    lambda x: "masaje" in x,
        "faciales":   lambda x: "facial" in x or "limpieza" in x,
        "depilacion": lambda x: "depilacion" in x,
        "corporales": lambda x: "reductivo" in x or "corporal" in x,
        "unas":       lambda x: "manicure" in x or "pedicure" in x or "una" in x,
        "pestanas":   lambda x: "pestana" in x or "ceja" in x,
        "cabello":    lambda x: "keratina" in x or "tinte" in x or "corte" in x or "cabello" in x,
    }
    fn = mapa.get(categoria)
    return fn(n) if fn else False


def interpretar_fecha(texto):
    texto_n = normalizar(texto)
    hoy = datetime.now()
    dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

    if "hoy" in texto_n:
        return hoy.strftime("%Y-%m-%d"), DIAS_ES.get(hoy.strftime("%A").lower(), "")
    if "manana" in texto_n or "mañana" in texto:
        manana = hoy + timedelta(days=1)
        return manana.strftime("%Y-%m-%d"), DIAS_ES.get(manana.strftime("%A").lower(), "")
    for dia in dias:
        if dia in texto_n:
            diff = (dias.index(dia) - hoy.weekday()) % 7 or 7
            fecha = hoy + timedelta(days=diff)
            return fecha.strftime("%Y-%m-%d"), dia
    try:
        fecha = datetime.strptime(texto_n, "%Y-%m-%d")
        return fecha.strftime("%Y-%m-%d"), DIAS_ES.get(fecha.strftime("%A").lower(), "")
    except Exception:
        return None, None

# ═══════════════════════════════════════════════════════════════
# EJECUCIÓN DE TOOLS
# ═══════════════════════════════════════════════════════════════
def ejecutar_tool(tool_name, tool_input, telefono_remitente):
    print(f"🔧 TOOL: {tool_name} | INPUT: {tool_input}")

    # ── get_servicios ──────────────────────────────────────────
    if tool_name == "get_servicios":
        categoria = tool_input.get("categoria", "").strip().lower()
        resultado = supabase.table("servicios").select("*").execute()
        servicios = resultado.data or []

        if categoria:
            servicios = [s for s in servicios if servicio_pertenece_categoria(s, categoria)]

        if not servicios:
            return {"error": "No encontré servicios para esa categoría."}

        lista = []
        for s in servicios:
            lista.append({
                "id":          s["id"],
                "nombre":      s["nombre"],
                "precio":      formatear_precio(s["precio"]),
                "descripcion": descripcion_servicio(s["nombre"]),
                "frase":       frase_alusiva(s["nombre"]),
            })
        return {"servicios": lista}

    # ── get_horarios ───────────────────────────────────────────
    elif tool_name == "get_horarios":
        fecha_texto = tool_input.get("fecha_texto", "")
        fecha_str, dia_semana = interpretar_fecha(fecha_texto)

        if not fecha_str:
            return {"error": "No pude interpretar la fecha. Pide al cliente que aclare el día."}
        if dia_semana == "domingo":
            return {"error": "Los domingos estamos cerrados. Pide otro día."}
        if dia_semana not in HORARIOS_DISPONIBLES:
            return {"error": f"No tenemos horarios para '{dia_semana}'. Pide un día válido."}

        horarios_base = HORARIOS_DISPONIBLES[dia_semana]
        inicio = f"{fecha_str} 00:00:00"
        fin    = f"{fecha_str} 23:59:59"

        try:
            citas = supabase.table("citas").select("fecha_hora") \
                .gte("fecha_hora", inicio).lte("fecha_hora", fin) \
                .neq("estado", "cancelada").execute()
            ocupadas = {str(c["fecha_hora"])[11:16] for c in citas.data}
            disponibles = [h for h in horarios_base if h not in ocupadas]
        except Exception as e:
            print(f"ERROR horarios: {e}")
            disponibles = horarios_base

        if not disponibles:
            return {
                "fecha": fecha_str,
                "dia":   dia_semana,
                "error": f"Para el {dia_semana} ya no hay cupos disponibles."
            }

        return {
            "fecha":       fecha_str,
            "dia":         dia_semana,
            "disponibles": disponibles
        }

    # ── guardar_cita ───────────────────────────────────────────
    elif tool_name == "guardar_cita":
        nombre   = tool_input["nombre_cliente"]
        telefono = tool_input["telefono"]
        nombre_s = tool_input["nombre_servicio"]
        fecha    = tool_input["fecha"]
        hora     = tool_input["hora"]

        # Buscar el servicio en BD
        resultado_s = supabase.table("servicios").select("*").execute()
        servicios   = resultado_s.data or []
        servicio    = next(
            (s for s in servicios if normalizar(s["nombre"]) == normalizar(nombre_s)),
            None
        )
        if not servicio:
            return {"error": f"No encontré el servicio '{nombre_s}' en el sistema."}

        # Buscar o crear cliente
        try:
            res_c = supabase.table("clientes").select("*").eq("telefono", telefono).execute()
            if res_c.data:
                cliente = res_c.data[0]
                if nombre and cliente.get("nombre") != nombre:
                    supabase.table("clientes").update({"nombre": nombre}).eq("id", cliente["id"]).execute()
                    cliente["nombre"] = nombre
            else:
                nuevo = supabase.table("clientes").insert({"nombre": nombre, "telefono": telefono}).execute()
                cliente = nuevo.data[0] if nuevo.data else None

            if not cliente:
                return {"error": "No pude registrar al cliente."}
        except Exception as e:
            print(f"ERROR cliente: {e}")
            return {"error": "Problema al registrar el cliente."}

        # Verificar que el horario sigue libre
        fecha_hora_completa = f"{fecha} {hora}:00"
        try:
            ocupado = supabase.table("citas").select("id") \
                .eq("fecha_hora", fecha_hora_completa) \
                .neq("estado", "cancelada").execute()
            if ocupado.data:
                return {"error": f"El horario {hora} del {fecha} acaba de ocuparse. Pide al cliente que elija otro."}
        except Exception as e:
            print(f"ERROR verificando horario: {e}")

        # Guardar cita
        try:
            cita = supabase.table("citas").insert({
                "cliente_id":  cliente["id"],
                "servicio_id": servicio["id"],
                "fecha_hora":  fecha_hora_completa,
                "estado":      "confirmada",
            }).execute()

            if cita.data:
                return {
                    "exito":    True,
                    "mensaje":  f"Cita confirmada para {nombre} — {nombre_s} el {fecha} a las {hora}.",
                    "cliente":  nombre,
                    "servicio": nombre_s,
                    "fecha":    fecha,
                    "hora":     hora,
                }
            else:
                return {"error": "Hubo un problema al guardar la cita."}
        except Exception as e:
            print(f"ERROR guardar cita: {e}")
            return {"error": "Error al guardar la cita en el sistema."}

    return {"error": f"Tool desconocida: {tool_name}"}


# ═══════════════════════════════════════════════════════════════
# LIMPIEZA DE HISTORIAL — elimina tool_results huérfanos
# Esto evita el error: "unexpected tool_use_id found in tool_result blocks"
# ═══════════════════════════════════════════════════════════════
def limpiar_historial(historial):
    """
    Filtra el historial para que nunca haya tool_results sin su tool_use correspondiente.
    Solo conserva turnos de texto puro (user string / assistant string).
    """
    limpio = []
    for msg in historial:
        content = msg.get("content", "")
        # Solo guardamos mensajes de texto plano — descartamos bloques de tools
        if isinstance(content, str):
            limpio.append(msg)
        elif isinstance(content, list):
            # Si todos los bloques son de texto, lo guardamos resumido
            textos = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if textos:
                limpio.append({"role": msg["role"], "content": " ".join(textos)})
            # Si son tool_use o tool_result los descartamos — evitan el error
    return limpio


# ═══════════════════════════════════════════════════════════════
# CICLO AGENTICO — Claude + tools hasta respuesta final
# ═══════════════════════════════════════════════════════════════
def responder(mensaje_usuario, historial, telefono):
    """
    Ciclo agéntico limpio:
    1. Limpia el historial de tool blocks huérfanos
    2. Claude recibe el mensaje + historial
    3. Si necesita datos, llama una tool
    4. Ejecutamos la tool y devolvemos el resultado
    5. Claude formula la respuesta final con personalidad de Valentina
    """
    # Limpiamos el historial antes de enviarlo — evita el bug de tool_use_id
    historial_limpio = limpiar_historial(historial)
    mensajes = list(historial_limpio[-12:])  # últimos 6 turnos de texto
    mensajes.append({"role": "user", "content": mensaje_usuario})

    MAX_ITERACIONES = 5
    iteracion = 0
    texto_final = ""

    while iteracion < MAX_ITERACIONES:
        iteracion += 1

        respuesta = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=700,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=mensajes,
        )

        print(f"  [iter {iteracion}] stop_reason={respuesta.stop_reason}")

        # ── Respuesta final de texto ───────────────────────────
        if respuesta.stop_reason == "end_turn":
            for bloque in respuesta.content:
                if hasattr(bloque, "text"):
                    texto_final += bloque.text
            break

        # ── Claude quiere usar una tool ────────────────────────
        if respuesta.stop_reason == "tool_use":
            # Convertimos el contenido a formato serializable para el historial interno
            content_serializable = []
            for bloque in respuesta.content:
                if bloque.type == "text":
                    content_serializable.append({"type": "text", "text": bloque.text})
                elif bloque.type == "tool_use":
                    content_serializable.append({
                        "type":  "tool_use",
                        "id":    bloque.id,
                        "name":  bloque.name,
                        "input": bloque.input,
                    })

            mensajes.append({"role": "assistant", "content": content_serializable})

            # Ejecutamos cada tool y recogemos resultados
            tool_results = []
            for bloque in respuesta.content:
                if bloque.type == "tool_use":
                    resultado = ejecutar_tool(bloque.name, bloque.input, telefono)
                    print(f"  → {bloque.name}: {json.dumps(resultado, ensure_ascii=False)[:150]}")
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": bloque.id,
                        "content":     json.dumps(resultado, ensure_ascii=False),
                    })

            mensajes.append({"role": "user", "content": tool_results})
            continue

        break  # stop reason inesperado

    texto_final = texto_final.strip()
    if not texto_final:
        texto_final = "Perdona, tuve un problema 😊 ¿Puedes repetirme tu consulta?"

    # Para el historial persistente solo guardamos texto plano
    return texto_final, mensaje_usuario


# ═══════════════════════════════════════════════════════════════
# ENDPOINT PRINCIPAL
# ═══════════════════════════════════════════════════════════════
@app.route("/bot", methods=["POST"])
def bot():
    mensaje   = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    telefono  = remitente.replace("whatsapp:", "")

    print(f"\n📩 {remitente}: {mensaje}")

    if remitente not in user_history:
        user_history[remitente] = []

    historial = user_history[remitente]

    try:
        respuesta_texto, msg_usuario = responder(mensaje, historial, telefono)
    except Exception as e:
        print(f"❌ ERROR: {e}")
        respuesta_texto = "Tuve un inconveniente 😊 ¿Puedes repetirme tu consulta?"
        msg_usuario = mensaje

    # Guardamos solo texto plano en el historial — nunca tool blocks
    user_history[remitente] = (historial + [
        {"role": "user",      "content": msg_usuario},
        {"role": "assistant", "content": respuesta_texto},
    ])[-24:]  # máximo 12 turnos

    print(f"📤 VALENTINA: {respuesta_texto}")

    resp = MessagingResponse()
    resp.message(respuesta_texto)
    return Response(str(resp), mimetype="application/xml")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
