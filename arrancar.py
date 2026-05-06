import os
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

user_states = {}
user_history = {}

SYSTEM_PROMPT = """
Eres Valentina, la recepcionista virtual de Spa Bella en WhatsApp.
Responde de forma cálida, breve y útil.

REGLA PRINCIPAL:
- La agenda, precios, servicios y horarios los maneja el sistema con reglas.
- Tú solo ayudas en dudas generales o mensajes fuera del flujo.
- No saludes ni te presentes si la conversación ya empezó.
- No inventes precios, promociones, horarios ni disponibilidad.
- No menciones recordatorios automáticos porque aún no están activos.
- Si no estás segura, ofrece pasar a una persona del spa.

SOBRE SPA BELLA:
- Servicios: masajes, faciales, depilación, uñas, pestañas, cabello y tratamientos corporales.
- Horarios: lunes a jueves 9am-6pm, viernes 9am-8pm, sábado 9am-4pm, domingos cerrado.
- Puedes orientar al cliente hacia servicios, precios, horarios o citas.

ESTILO:
- Máximo 3 líneas.
- Usa máximo 1 emoji.
- Termina con una pregunta útil cuando aplique.
"""

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

PALABRAS_CLAVE = {
    "masaje relajante": ["relajante", "relajacion", "relajarme", "estres", "descansar", "relajar"],
    "masaje descontracturante": ["descontracturante", "contractura", "dolor", "espalda", "nudos", "tension"],
    "masaje con piedras calientes": ["piedras", "calientes", "piedras calientes"],
    "facial hidratante": ["hidratante", "hidratacion", "piel seca", "hidratarme", "hidrata"],
    "facial anti-edad": ["anti edad", "antiedad", "arrugas", "rejuvenecer", "anti-edad"],
    "depilacion de axilas": ["axila", "axilas"],
    "depilacion de piernas": ["pierna", "piernas", "piernas completas"],
    "depilacion de ingles": ["ingles", "bikini"],
    "tratamiento reductivo": ["reductivo", "reducir", "adelgazar", "medidas", "corporal"],
    "diseno de pestanas": ["pestanas", "pestañas", "cejas", "lifting"],
    "manicure clasico": ["manicure", "clasico", "unas", "uñas"],
    "manicure semipermanente": ["semipermanente", "semi permanente"],
    "keratina": ["keratina", "alisado"],
    "tinte + corte": ["tinte", "corte", "cabello", "pelo"],
}

CATEGORIAS = {
    "masajes": ["masaje", "masajes", "relajacion", "dolor muscular"],
    "faciales": ["facial", "faciales", "cara", "rostro", "piel"],
    "depilacion": ["depilacion", "depilar", "vello", "cera"],
    "unas": ["manicure", "pedicure", "unas", "uñas"],
    "pestanas": ["pestanas", "pestañas", "cejas"],
    "cabello": ["keratina", "tinte", "cabello", "pelo"],
    "corporales": ["reductivo", "corporal", "cuerpo"],
}


def normalizar_texto(texto):
    texto = str(texto or "").lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    for ch in ["?", "¿", "!", "¡", ".", ",", ";", ":", "-", "_"]:
        texto = texto.replace(ch, " ")
    return " ".join(texto.split())


def formatear_precio(valor):
    try:
        return f"${int(valor):,}".replace(",", ".")
    except Exception:
        return f"${valor}"


def es_saludo_puro(mensaje_norm):
    saludos = ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hey"]
    return mensaje_norm in saludos


def cargar_servicios():
    resultado = supabase.table("servicios").select("*").eq("activo", True).execute()
    return resultado.data or []


def servicio_en_categoria(servicio, categoria):
    n = normalizar_texto(servicio.get("nombre", ""))
    if categoria == "masajes":
        return "masaje" in n
    if categoria == "faciales":
        return "facial" in n
    if categoria == "depilacion":
        return "depilacion" in n
    if categoria == "unas":
        return "manicure" in n or "pedicure" in n
    if categoria == "pestanas":
        return "pestanas" in n or "pestañas" in n
    if categoria == "cabello":
        return "keratina" in n or "tinte" in n or "corte" in n
    if categoria == "corporales":
        return "reductivo" in n or "corporal" in n
    return False


def buscar_servicio(mensaje):
    servicios = cargar_servicios()
    mensaje_norm = normalizar_texto(mensaje)

    # 1. Nombre completo o parcial del servicio.
    for servicio in servicios:
        nombre_norm = normalizar_texto(servicio["nombre"])
        if nombre_norm in mensaje_norm or mensaje_norm in nombre_norm:
            return {"tipo": "servicio", "data": servicio}

    # 2. Palabras clave específicas.
    for servicio in servicios:
        nombre_norm = normalizar_texto(servicio["nombre"])
        for nombre_servicio, keywords in PALABRAS_CLAVE.items():
            if normalizar_texto(nombre_servicio) == nombre_norm:
                for palabra in keywords:
                    if normalizar_texto(palabra) in mensaje_norm:
                        return {"tipo": "servicio", "data": servicio}

    # 3. Categoría.
    for categoria, keywords in CATEGORIAS.items():
        for palabra in keywords:
            if normalizar_texto(palabra) in mensaje_norm:
                servicios_cat = [s for s in servicios if servicio_en_categoria(s, categoria)]
                if servicios_cat:
                    return {"tipo": "categoria", "categoria": categoria, "data": servicios_cat}

    return None


def construir_menu_servicios(servicios):
    texto = ""
    for i, servicio in enumerate(servicios, start=1):
        texto += f"{i}. {servicio['nombre']} - {formatear_precio(servicio['precio'])}\n"
    return texto.strip()


def seleccionar_opcion_servicio(mensaje, opciones):
    mensaje_norm = normalizar_texto(mensaje)

    # Número: 1, 2, 3...
    if mensaje_norm.isdigit():
        indice = int(mensaje_norm) - 1
        if 0 <= indice < len(opciones):
            return opciones[indice]

    # Texto: "piernas", "relajante", "solo axilas", etc.
    for servicio in opciones:
        nombre_norm = normalizar_texto(servicio["nombre"])
        if nombre_norm in mensaje_norm or mensaje_norm in nombre_norm:
            return servicio

        palabras = [p for p in nombre_norm.split() if len(p) > 3]
        coincidencias = [p for p in palabras if p in mensaje_norm]
        if coincidencias:
            return servicio

    # Casos frecuentes.
    equivalencias = {
        "pierna": "pierna",
        "piernas": "pierna",
        "axila": "axila",
        "axilas": "axila",
        "relajante": "relajante",
        "descontracturante": "descontracturante",
        "hidratante": "hidratante",
        "anti edad": "anti",
        "antiedad": "anti",
        "keratina": "keratina",
        "semipermanente": "semipermanente",
    }
    for clave, buscar in equivalencias.items():
        if clave in mensaje_norm:
            for servicio in opciones:
                if buscar in normalizar_texto(servicio["nombre"]):
                    return servicio

    return None


def consultar_claude(mensaje, historial=None):
    historial = historial or []
    mensajes = []
    for h in historial[-6:]:
        mensajes.append(h)
    mensajes.append({"role": "user", "content": mensaje})
    respuesta = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=120,
        temperature=0.2,
        system=SYSTEM_PROMPT,
        messages=mensajes,
    )
    return respuesta.content[0].text.strip()


def buscar_o_crear_cliente(nombre, telefono):
    resultado = supabase.table("clientes").select("*").eq("telefono", telefono).execute()
    if resultado.data:
        cliente = resultado.data[0]
        # Actualiza el nombre si estaba vacío o si quieres conservar el último nombre escrito.
        if nombre and cliente.get("nombre") != nombre:
            supabase.table("clientes").update({"nombre": nombre}).eq("id", cliente["id"]).execute()
            cliente["nombre"] = nombre
        return cliente

    nuevo = supabase.table("clientes").insert({
        "nombre": nombre,
        "telefono": telefono,
    }).execute()
    return nuevo.data[0] if nuevo.data else None


def guardar_cita(cliente_id, servicio_id, fecha_hora):
    try:
        cita = supabase.table("citas").insert({
            "cliente_id": cliente_id,
            "servicio_id": servicio_id,
            "fecha_hora": fecha_hora,
            "estado": "pendiente",
        }).execute()
        return cita.data[0] if cita.data else None
    except Exception as e:
        print(f"ERROR guardando cita: {e}")
        return None


def horario_ocupado(fecha_hora):
    try:
        resultado = supabase.table("citas") \
            .select("id") \
            .eq("fecha_hora", fecha_hora) \
            .neq("estado", "cancelada") \
            .execute()
        return len(resultado.data) > 0
    except Exception as e:
        print(f"ERROR consultando horario ocupado: {e}")
        return True


def obtener_horarios_disponibles(fecha_str, dia_semana):
    try:
        horarios_base = HORARIOS_DISPONIBLES.get(dia_semana, [])
        inicio_dia = f"{fecha_str} 00:00:00"
        fin_dia = f"{fecha_str} 23:59:59"
        citas = supabase.table("citas") \
            .select("fecha_hora") \
            .gte("fecha_hora", inicio_dia) \
            .lte("fecha_hora", fin_dia) \
            .neq("estado", "cancelada") \
            .execute()
        horas_ocupadas = [str(cita["fecha_hora"])[11:16] for cita in (citas.data or [])]
        return [h for h in horarios_base if h not in horas_ocupadas]
    except Exception as e:
        print(f"ERROR obteniendo horarios disponibles: {e}")
        return []


def interpretar_fecha(texto):
    texto_norm = normalizar_texto(texto)
    hoy = datetime.now()
    dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

    if "hoy" in texto_norm:
        return hoy.strftime("%Y-%m-%d"), DIAS_ES.get(hoy.strftime("%A").lower(), "")

    if "manana" in texto_norm:
        manana = hoy + timedelta(days=1)
        return manana.strftime("%Y-%m-%d"), DIAS_ES.get(manana.strftime("%A").lower(), "")

    for dia in dias_semana:
        if dia in texto_norm:
            dias_hasta = (dias_semana.index(dia) - hoy.weekday()) % 7
            if dias_hasta == 0:
                dias_hasta = 7
            fecha = hoy + timedelta(days=dias_hasta)
            return fecha.strftime("%Y-%m-%d"), dia

    try:
        fecha = datetime.strptime(texto_norm, "%Y-%m-%d")
        dia = DIAS_ES.get(fecha.strftime("%A").lower(), "")
        return fecha.strftime("%Y-%m-%d"), dia
    except Exception:
        return None, None


def estado_inicial(saludado=False):
    return {
        "intent": None,
        "step": "inicio",
        "servicio": None,
        "fecha": None,
        "fecha_texto": None,
        "hora": None,
        "nombre": None,
        "opciones": [],
        "categoria_actual": None,
        "saludado": saludado,
    }


def reset_estado(remitente):
    user_states[remitente] = estado_inicial(saludado=False)
    user_history[remitente] = []


def limpiar_flujo(remitente):
    saludado = user_states.get(remitente, {}).get("saludado", False)
    user_states[remitente] = estado_inicial(saludado=saludado)
    user_history[remitente] = []


def responder_categoria(state, categoria_nombre, categoria_servicios, modo_agendar=False):
    state["opciones"] = categoria_servicios
    state["categoria_actual"] = categoria_nombre
    state["step"] = "seleccionando_servicio"
    if modo_agendar:
        state["intent"] = "agendar"
    else:
        state["intent"] = "servicio_mencionado"

    menu = construir_menu_servicios(categoria_servicios)
    return f"""Tenemos estas opciones de {categoria_nombre} 😊

{menu}

Responde con el número de la opción que te interesa."""


def responder_servicio_elegido(state, servicio):
    state["servicio"] = servicio
    state["opciones"] = []
    state["categoria_actual"] = None
    state["intent"] = "servicio_mencionado"
    state["step"] = "servicio_confirmado"
    precio = formatear_precio(servicio["precio"])
    return f"""{servicio['nombre']} cuesta {precio} 😊

Responde 1 para agendar o 2 para ver otros servicios."""


@app.route("/bot", methods=["POST"])
def bot():
    mensaje = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    print(f"\nMENSAJE de {remitente}: {mensaje}")

    mensaje_norm = normalizar_texto(mensaje)

    if remitente not in user_states:
        reset_estado(remitente)
    if remitente not in user_history:
        user_history[remitente] = []

    state = user_states[remitente]

    resultado_servicio = buscar_servicio(mensaje)
    servicio = resultado_servicio["data"] if resultado_servicio and resultado_servicio["tipo"] == "servicio" else None
    categoria_servicios = resultado_servicio["data"] if resultado_servicio and resultado_servicio["tipo"] == "categoria" else None
    categoria_nombre = resultado_servicio["categoria"] if resultado_servicio and resultado_servicio["tipo"] == "categoria" else None

    # CANCELAR / REINICIAR
    if any(p in mensaje_norm for p in ["cancelar", "reiniciar", "empezar de nuevo", "volver al inicio"]):
        limpiar_flujo(remitente)
        respuesta_texto = "Claro 😊 Empecemos de nuevo. ¿Quieres consultar servicios, precios o agendar una cita?"

    # SALUDO PURO: no debe robar mensajes como "hola quiero agendar".
    elif es_saludo_puro(mensaje_norm):
        if state.get("saludado"):
            respuesta_texto = "Hola de nuevo 😊 ¿Quieres consultar servicios, precios o agendar una cita?"
        else:
            state["saludado"] = True
            respuesta_texto = """¡Hola! Bienvenida a Spa Bella 🌸

Soy Valentina. Puedo ayudarte con servicios, precios, horarios y citas.

¿Qué estás buscando hoy?"""

    # SELECCIÓN DE MENÚ NUMÉRICO O TEXTO.
    elif state.get("step") == "seleccionando_servicio" and state.get("opciones"):
        servicio_elegido = seleccionar_opcion_servicio(mensaje, state["opciones"])
        if servicio_elegido:
            if state.get("intent") == "agendar":
                state["servicio"] = servicio_elegido
                state["opciones"] = []
                state["categoria_actual"] = None
                state["step"] = "esperando_fecha"
                respuesta_texto = f"Perfecto ✨ {servicio_elegido['nombre']} seleccionado. ¿Para qué día lo quieres?"
            else:
                respuesta_texto = responder_servicio_elegido(state, servicio_elegido)
        else:
            menu = construir_menu_servicios(state["opciones"])
            respuesta_texto = f"""No logré identificar cuál opción quieres 😊

Elige una opción con el número:
{menu}"""

    # DESPUÉS DE MOSTRAR PRECIO DE UN SERVICIO.
    elif state.get("step") == "servicio_confirmado":
        if mensaje_norm in ["1", "si", "sí", "agendar", "quiero agendar", "cita", "reservar"]:
            state["intent"] = "agendar"
            state["step"] = "esperando_fecha"
            respuesta_texto = f"Perfecto 😊 ¿Para qué día quieres tu {state['servicio']['nombre']}?"
        elif mensaje_norm in ["2", "otro", "otros", "ver otro", "ver otros", "servicios"]:
            limpiar_flujo(remitente)
            user_states[remitente]["saludado"] = True
            respuesta_texto = "Claro 😊 ¿Qué quieres revisar: masajes, faciales o depilación?"
        elif any(p in mensaje_norm for p in ["precio", "cuanto", "cuesta", "valor"]):
            servicio_actual = state.get("servicio")
            precio = formatear_precio(servicio_actual["precio"])
            respuesta_texto = f"{servicio_actual['nombre']} cuesta {precio} 😊 ¿Quieres agendarlo? Responde 1 para agendar."
        else:
            respuesta_texto = "¿Quieres agendarlo? Responde 1 para agendar o 2 para ver otros servicios 😊"

    # FLUJO DE AGENDAMIENTO ACTIVO.
    elif state.get("intent") == "agendar":

        if state.get("step") == "esperando_servicio":
            if servicio:
                state["servicio"] = servicio
                state["step"] = "esperando_fecha"
                respuesta_texto = f"Perfecto ✨ {servicio['nombre']} seleccionado. ¿Para qué día lo quieres?"
            elif categoria_servicios:
                respuesta_texto = responder_categoria(state, categoria_nombre, categoria_servicios, modo_agendar=True)
            else:
                respuesta_texto = "¿Para qué servicio quieres la cita? Puedes escribir masaje, facial o depilación 😊"

        elif state.get("step") == "esperando_fecha":
            fecha_str, dia_semana = interpretar_fecha(mensaje)
            if fecha_str and dia_semana and dia_semana in HORARIOS_DISPONIBLES:
                state["fecha"] = fecha_str
                state["fecha_texto"] = f"{dia_semana} {fecha_str}"
                state["step"] = "esperando_hora"
                horarios_disponibles = obtener_horarios_disponibles(fecha_str, dia_semana)
                if horarios_disponibles:
                    horarios = " · ".join(horarios_disponibles)
                    respuesta_texto = f"Para el {dia_semana} tenemos estos horarios disponibles 😊\n\n{horarios}\n\n¿Cuál te viene mejor?"
                else:
                    state["step"] = "esperando_fecha"
                    respuesta_texto = f"Para el {dia_semana} ya no tenemos horarios disponibles 😊 ¿Qué otro día te gustaría?"
            elif dia_semana == "domingo":
                respuesta_texto = "Los domingos estamos cerrados 😊 ¿Te parece bien otro día? Atendemos de lunes a sábado."
            else:
                respuesta_texto = "No entendí bien la fecha 😊 Puedes decirme: mañana, viernes o sábado."

        elif state.get("step") == "esperando_hora":
            hora_limpia = mensaje_norm.replace("am", "").replace("pm", "").replace(" ", "")
            if ":" not in hora_limpia:
                try:
                    h = int(hora_limpia)
                    hora_limpia = f"{h:02d}:00"
                except Exception:
                    pass

            dia_semana = state["fecha_texto"].split(" ")[0] if state.get("fecha_texto") else ""
            horarios_dia = obtener_horarios_disponibles(state.get("fecha"), dia_semana)

            if hora_limpia in horarios_dia:
                state["hora"] = hora_limpia
                state["step"] = "esperando_nombre"
                respuesta_texto = f"¡Perfecto! {hora_limpia} anotado ✨\n\n¿A nombre de quién quedo la cita?"
            else:
                horarios = " · ".join(horarios_dia) if horarios_dia else "ninguno disponible"
                respuesta_texto = f"Ese horario no está disponible 😊\n\nDisponibles: {horarios}\n\n¿Cuál prefieres?"

        elif state.get("step") == "esperando_nombre":
            nombre_cliente = mensaje.strip()
            telefono = remitente.replace("whatsapp:", "")
            try:
                cliente = buscar_o_crear_cliente(nombre_cliente, telefono)
                fecha_hora_completa = f"{state['fecha']} {state['hora']}:00"

                if horario_ocupado(fecha_hora_completa):
                    dia = state["fecha_texto"].split(" ")[0]
                    horarios_disponibles = obtener_horarios_disponibles(state["fecha"], dia)
                    if horarios_disponibles:
                        horarios = " · ".join(horarios_disponibles)
                        state["step"] = "esperando_hora"
                        respuesta_texto = f"Ese horario acaba de ocuparse 😊 Para el {dia} tenemos disponible:\n\n{horarios}\n\n¿Cuál prefieres?"
                    else:
                        state["step"] = "esperando_fecha"
                        respuesta_texto = f"Ese día ya no tiene horarios disponibles 😊 ¿Qué otro día te gustaría?"
                else:
                    cita = guardar_cita(cliente["id"], state["servicio"]["id"], fecha_hora_completa)
                    if cita:
                        dia = state["fecha_texto"].split(" ")[0]
                        respuesta_texto = f"""¡Listo {nombre_cliente}! 🎉 Tu cita quedó confirmada:

📋 {state['servicio']['nombre']}
📅 {dia.capitalize()} a las {state['hora']}
💆 Te esperamos en Spa Bella

¿Necesitas algo más?"""
                        limpiar_flujo(remitente)
                    else:
                        respuesta_texto = "Hubo un problema al guardar tu cita 😊 ¿Puedes intentarlo de nuevo?"
            except Exception as e:
                print(f"ERROR en agendamiento: {e}")
                respuesta_texto = "Tuve un inconveniente 😊 ¿Me repites tu nombre para intentarlo de nuevo?"

        else:
            state["step"] = "esperando_servicio"
            respuesta_texto = "¿Para qué servicio deseas agendar tu cita? 😊"

    # INICIAR AGENDAMIENTO.
    elif any(p in mensaje_norm for p in ["cita", "agendar", "reservar", "turno", "quiero agendar"]):
        state["intent"] = "agendar"
        if servicio:
            state["servicio"] = servicio
            state["step"] = "esperando_fecha"
            respuesta_texto = f"Con gusto ✨ {servicio['nombre']} seleccionado. ¿Para qué día lo quieres?"
        elif categoria_servicios:
            respuesta_texto = responder_categoria(state, categoria_nombre, categoria_servicios, modo_agendar=True)
        else:
            state["step"] = "esperando_servicio"
            respuesta_texto = "Con gusto te agendo 😊 ¿Para qué servicio quieres la cita?"

    # PRECIOS.
    elif any(p in mensaje_norm for p in ["precio", "precios", "valor", "cuanto", "cuesta", "cuestan", "tarifa"]):
        state["intent"] = "precio"
        if servicio:
            state["servicio"] = servicio
            state["step"] = "servicio_confirmado"
            precio = formatear_precio(servicio["precio"])
            respuesta_texto = f"{servicio['nombre']} cuesta {precio} 😊\n\nResponde 1 para agendar o 2 para ver otros servicios."
        elif categoria_servicios:
            respuesta_texto = responder_categoria(state, categoria_nombre, categoria_servicios, modo_agendar=False)
        elif state.get("servicio"):
            servicio_actual = state["servicio"]
            precio = formatear_precio(servicio_actual["precio"])
            state["step"] = "servicio_confirmado"
            respuesta_texto = f"{servicio_actual['nombre']} cuesta {precio} 😊\n\nResponde 1 para agendar o 2 para ver otros servicios."
        else:
            state["step"] = "esperando_servicio_precio"
            respuesta_texto = "Claro 😊 ¿De qué servicio quieres saber el precio? Puedes escribir masaje, facial o depilación."

    # SI ESTABA ESPERANDO SERVICIO PARA PRECIO.
    elif state.get("step") == "esperando_servicio_precio":
        if servicio:
            respuesta_texto = responder_servicio_elegido(state, servicio)
        elif categoria_servicios:
            respuesta_texto = responder_categoria(state, categoria_nombre, categoria_servicios, modo_agendar=False)
        else:
            respuesta_texto = "No identifiqué el servicio 😊 Puedes escribir, por ejemplo: masaje relajante, facial hidratante o depilación piernas."

    # SERVICIOS GENERALES.
    elif any(p in mensaje_norm for p in ["servicio", "servicios", "tratamiento", "tratamientos", "que tienen", "que ofrecen"]):
        servicios = cargar_servicios()
        menu = construir_menu_servicios(servicios)
        state["opciones"] = servicios
        state["step"] = "seleccionando_servicio"
        state["intent"] = "servicio_mencionado"
        respuesta_texto = f"""Estos son nuestros servicios en Spa Bella 😊

{menu}

Responde con el número del servicio que te interesa."""

    # SERVICIO O CATEGORÍA MENCIONADA SIN CONTEXTO.
    elif resultado_servicio:
        if servicio:
            respuesta_texto = responder_servicio_elegido(state, servicio)
        elif categoria_servicios:
            respuesta_texto = responder_categoria(state, categoria_nombre, categoria_servicios, modo_agendar=False)

    # HORARIOS.
    elif any(p in mensaje_norm for p in ["horario", "horarios", "atienden", "abierto", "abren", "cierran", "dias"]):
        respuesta_texto = """Nuestro horario es:

- Lunes a jueves: 9am - 6pm
- Viernes: 9am - 8pm
- Sábado: 9am - 4pm
- Domingo: cerrado

¿Te gustaría agendar una cita? 😊"""

    # PROMOCIONES.
    elif any(p in mensaje_norm for p in ["promo", "promocion", "promociones", "descuento", "oferta"]):
        respuesta_texto = "Tenemos promociones en servicios seleccionados 😊 ¿Te interesa facial, masaje, depilación o tratamiento corporal?"

    # DESPEDIDAS.
    elif any(p in mensaje_norm for p in ["adios", "chao", "hasta luego", "gracias", "muchas gracias"]):
        limpiar_flujo(remitente)
        respuesta_texto = "¡Con mucho gusto! 😊 Si necesitas algo de Spa Bella, aquí estaré."

    # FALLBACK IA SOLO PARA DUDAS GENERALES.
    else:
        try:
            respuesta_texto = consultar_claude(mensaje, historial=user_history[remitente])
            user_history[remitente].append({"role": "user", "content": mensaje})
            user_history[remitente].append({"role": "assistant", "content": respuesta_texto})
            if len(user_history[remitente]) > 20:
                user_history[remitente] = user_history[remitente][-20:]
            print(f"VALENTINA: {respuesta_texto}")
        except Exception as e:
            print(f"ERROR CLAUDE: {e}")
            respuesta_texto = "No logré entenderte bien 😊 ¿Quieres consultar servicios, precios, horarios o agendar una cita?"

    respuesta = MessagingResponse()
    respuesta.message(respuesta_texto)
    print(f"ENVIANDO: {str(respuesta)}")
    return Response(str(respuesta), mimetype="application/xml")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
