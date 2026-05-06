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
Eres Valentina, recepcionista virtual de Spa Bella en WhatsApp.
Tu trabajo es ayudar con servicios, precios, horarios y citas del spa.
Responde cálido, breve y sin sonar robótica.

Reglas:
- Máximo 3 líneas cuando sea posible.
- No inventes precios, promociones ni servicios.
- No respondas política, medicina, religión, noticias ni temas personales.
- Si algo no es del spa, redirige con amabilidad.
- No digas que eres IA o bot.
- No repitas la bienvenida si la conversación ya inició.
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

CATEGORIA_LABELS = {
    "masajes": "masajes",
    "faciales": "faciales",
    "depilacion": "depilación",
    "corporales": "tratamientos corporales",
    "unas": "uñas",
    "pestanas": "pestañas",
    "cabello": "cabello",
}

CATEGORIA_MENU = [
    {"key": "masajes", "label": "Masajes"},
    {"key": "faciales", "label": "Faciales"},
    {"key": "depilacion", "label": "Depilación"},
    {"key": "corporales", "label": "Tratamientos corporales"},
    {"key": "unas", "label": "Uñas"},
    {"key": "pestanas", "label": "Pestañas"},
    {"key": "cabello", "label": "Cabello"},
]

PALABRAS_CLAVE = {
    "masaje relajante": ["relajante", "relajacion", "relajarme", "estres", "descansar", "relajar"],
    "masaje descontracturante": ["descontracturante", "contractura", "dolor", "espalda", "nudos", "tension"],
    "masaje con piedras calientes": ["piedras", "calientes", "piedras calientes"],
    "facial hidratante": ["hidratante", "hidratacion", "piel seca", "hidratarme", "hidrata"],
    "facial anti-edad": ["anti edad", "antiedad", "arrugas", "rejuvenecer", "anti-edad"],
    "depilacion de axilas": ["axila", "axilas"],
    "depilacion de piernas": ["pierna", "piernas", "completa", "completas"],
    "depilacion de ingles": ["ingles", "bikini"],
    "tratamiento reductivo": ["reductivo", "reducir", "adelgazar", "medidas", "corporal"],
    "diseno de pestanas": ["pestanas", "pestanas", "cejas", "lifting"],
    "manicure clasico": ["manicure", "clasico", "unas", "uñas"],
    "manicure semipermanente": ["semipermanente", "semi permanente"],
    "keratina": ["keratina", "alisado"],
    "tinte + corte": ["tinte", "corte", "cabello", "pelo"],
}

CATEGORIAS = {
    "masajes": ["masaje", "masajes", "relajacion", "dolor muscular", "espalda"],
    "faciales": ["facial", "faciales", "cara", "rostro", "piel"],
    "depilacion": ["depilacion", "depilar", "vello", "cera"],
    "corporales": ["reductivo", "corporal", "cuerpo", "medidas"],
    "unas": ["manicure", "pedicure", "unas", "uñas"],
    "pestanas": ["pestanas", "pestañas", "cejas"],
    "cabello": ["keratina", "tinte", "corte", "cabello", "pelo"],
}

DESCRIPCIONES_SERVICIOS = {
    "masaje relajante": "Ideal para soltar tensión, descansar y desconectarte.",
    "masaje descontracturante": "Recomendado para espalda cargada, nudos o tensión muscular.",
    "masaje con piedras calientes": "Combina calor y masaje para una relajación más profunda.",
    "facial hidratante": "Ayuda a refrescar la piel y darle una apariencia más luminosa.",
    "facial anti-edad": "Pensado para cuidar la piel madura y mejorar su apariencia.",
    "depilacion de axilas": "Servicio rápido para una zona más limpia y cómoda.",
    "depilacion de piernas": "Ideal para dejar la piel de las piernas más suave y limpia.",
    "depilacion piernas completas": "Ideal para dejar la piel de las piernas más suave y limpia.",
    "depilacion de ingles": "Depilación de zona bikini realizada con cuidado y discreción.",
    "tratamiento reductivo": "Ayuda a trabajar zonas localizadas y complementar hábitos saludables.",
    "diseno de pestanas": "Realza la mirada con un acabado más definido.",
    "manicure clasico": "Limpieza, arreglo y esmaltado tradicional de uñas.",
    "manicure semipermanente": "Esmaltado de mayor duración para mantener las uñas arregladas por más tiempo.",
    "keratina": "Tratamiento capilar para ayudar a controlar el frizz y suavizar el cabello.",
    "tinte + corte": "Cambio de color y ajuste de corte para renovar tu estilo.",
}

TEMAS_FUERA = [
    "maduro", "presidente", "politica", "política", "gobierno", "elecciones",
    "futbol", "fútbol", "noticias", "religion", "religión", "dolar", "dólar",
    "clima", "pais", "país", "venezuela", "colombia", "trump", "petro"
]

PALABRAS_HUMANO = ["humano", "asesor", "asesora", "persona", "llamar", "me llaman", "hablar con alguien", "queja"]


def normalizar_texto(texto):
    texto = (texto or "").lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto


def formatear_precio(valor):
    try:
        return f"${int(valor):,}".replace(",", ".")
    except Exception:
        return f"${valor}"


def descripcion_servicio(nombre):
    return DESCRIPCIONES_SERVICIOS.get(normalizar_texto(nombre), "Es una opción muy solicitada en Spa Bella.")


def obtener_servicios():
    resultado = supabase.table("servicios").select("*").execute()
    return resultado.data or []


def servicio_pertenece_categoria(servicio, categoria):
    n = normalizar_texto(servicio.get("nombre", ""))
    if categoria == "masajes":
        return "masaje" in n
    if categoria == "faciales":
        return "facial" in n
    if categoria == "depilacion":
        return "depilacion" in n
    if categoria == "corporales":
        return "reductivo" in n or "corporal" in n
    if categoria == "unas":
        return "manicure" in n or "pedicure" in n or "una" in n
    if categoria == "pestanas":
        return "pestana" in n or "ceja" in n
    if categoria == "cabello":
        return "keratina" in n or "tinte" in n or "corte" in n or "cabello" in n
    return False


def servicios_por_categoria(categoria):
    return [s for s in obtener_servicios() if servicio_pertenece_categoria(s, categoria)]


def construir_menu_servicios(servicios):
    lineas = []
    for i, servicio in enumerate(servicios, start=1):
        lineas.append(f"{i}. {servicio['nombre']} - {formatear_precio(servicio['precio'])}")
    return "\n".join(lineas)


def construir_menu_categorias():
    return "\n".join([f"{i}. {item['label']}" for i, item in enumerate(CATEGORIA_MENU, start=1)])


def buscar_servicio(mensaje):
    data = obtener_servicios()
    mensaje_norm = normalizar_texto(mensaje)

    for servicio in data:
        nombre_norm = normalizar_texto(servicio["nombre"])
        if nombre_norm in mensaje_norm or mensaje_norm in nombre_norm:
            return {"tipo": "servicio", "data": servicio}

    for servicio in data:
        nombre_norm = normalizar_texto(servicio["nombre"])
        for nombre_servicio, keywords in PALABRAS_CLAVE.items():
            if normalizar_texto(nombre_servicio) == nombre_norm:
                for palabra in keywords:
                    if normalizar_texto(palabra) in mensaje_norm:
                        return {"tipo": "servicio", "data": servicio}

    for categoria, keywords in CATEGORIAS.items():
        for palabra in keywords:
            if normalizar_texto(palabra) in mensaje_norm:
                lista = servicios_por_categoria(categoria)
                if lista:
                    return {"tipo": "categoria", "categoria": categoria, "data": lista}

    return None


def seleccionar_opcion_servicio(mensaje, opciones):
    mensaje_norm = normalizar_texto(mensaje)

    if mensaje_norm.isdigit():
        idx = int(mensaje_norm) - 1
        if 0 <= idx < len(opciones):
            return opciones[idx]

    # Casos frecuentes escritos con texto.
    equivalencias = {
        "pierna": "pierna", "piernas": "pierna", "completa": "pierna", "completas": "pierna",
        "axila": "axila", "axilas": "axila",
        "relajante": "relajante", "descontracturante": "descontracturante",
        "hidratante": "hidratante", "anti edad": "anti", "antiedad": "anti",
    }

    for clave, objetivo in equivalencias.items():
        if clave in mensaje_norm:
            for servicio in opciones:
                if objetivo in normalizar_texto(servicio["nombre"]):
                    return servicio

    for servicio in opciones:
        nombre_norm = normalizar_texto(servicio["nombre"])
        if nombre_norm in mensaje_norm or mensaje_norm in nombre_norm:
            return servicio
        partes = [p for p in nombre_norm.split() if len(p) > 3]
        if any(p in mensaje_norm for p in partes):
            return servicio

    return None


def seleccionar_categoria(mensaje):
    mensaje_norm = normalizar_texto(mensaje)
    if mensaje_norm.isdigit():
        idx = int(mensaje_norm) - 1
        if 0 <= idx < len(CATEGORIA_MENU):
            return CATEGORIA_MENU[idx]["key"]
    for categoria, keywords in CATEGORIAS.items():
        if any(normalizar_texto(p) in mensaje_norm for p in keywords):
            return categoria
    return None


def es_fuera_de_tema(mensaje):
    msg = normalizar_texto(mensaje)
    return any(normalizar_texto(p) in msg for p in TEMAS_FUERA)


def quiere_servicios(mensaje):
    msg = normalizar_texto(mensaje)
    patrones = ["servicio", "servicios", "opciones", "todo", "todas", "que tienen", "que ofrecen", "tratamientos"]
    return any(p in msg for p in patrones)


def quiere_precio(mensaje):
    msg = normalizar_texto(mensaje)
    patrones = ["precio", "precios", "valor", "cuanto", "cuesta", "cuestan", "tarifa"]
    return any(p in msg for p in patrones)


def quiere_agendar(mensaje):
    msg = normalizar_texto(mensaje)
    patrones = ["cita", "agendar", "reservar", "turno", "quiero agendar", "agenda"]
    return any(p in msg for p in patrones)


def respuesta_menu_principal():
    return f"""¿Qué quieres revisar? 😊

{construir_menu_categorias()}

Responde con el número o el nombre de la categoría."""


def mostrar_categoria(categoria, state):
    servicios = servicios_por_categoria(categoria)
    if not servicios:
        state["step"] = "inicio"
        return "Por ahora no tengo opciones en esa categoría 😊 ¿Quieres revisar masajes, faciales o depilación?"

    state["step"] = "seleccionando_servicio"
    state["opciones"] = servicios
    state["categoria_actual"] = categoria
    etiqueta = CATEGORIA_LABELS.get(categoria, categoria)
    return f"""Tenemos estas opciones de {etiqueta} 😊

{construir_menu_servicios(servicios)}

Responde con el número de la opción que te interesa."""


def respuesta_servicio_confirmado(servicio, state):
    state["step"] = "servicio_confirmado"
    state["intent"] = "servicio"
    state["servicio"] = servicio
    nombre = servicio["nombre"]
    precio = formatear_precio(servicio["precio"])
    desc = descripcion_servicio(nombre)
    return f"""{nombre} cuesta {precio} 😊
{desc}

Responde 1 para agendar o 2 para ver otros servicios."""


def consultar_claude(mensaje, historial=[]):
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
        # Actualiza nombre si cambió o estaba vacío.
        if nombre and cliente.get("nombre") != nombre:
            supabase.table("clientes").update({"nombre": nombre}).eq("id", cliente["id"]).execute()
            cliente["nombre"] = nombre
        return cliente
    nuevo = supabase.table("clientes").insert({"nombre": nombre, "telefono": telefono}).execute()
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
        resultado = supabase.table("citas").select("id").eq("fecha_hora", fecha_hora).neq("estado", "cancelada").execute()
        return len(resultado.data) > 0
    except Exception as e:
        print(f"ERROR consultando horario ocupado: {e}")
        return True


def obtener_horarios_disponibles(fecha_str, dia_semana):
    try:
        horarios_base = HORARIOS_DISPONIBLES.get(dia_semana, [])
        inicio_dia = f"{fecha_str} 00:00:00"
        fin_dia = f"{fecha_str} 23:59:59"
        citas = supabase.table("citas").select("fecha_hora").gte("fecha_hora", inicio_dia).lte("fecha_hora", fin_dia).neq("estado", "cancelada").execute()
        horas_ocupadas = [str(cita["fecha_hora"])[11:16] for cita in citas.data]
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


def reset_estado(remitente):
    user_states[remitente] = {
        "intent": None,
        "step": "inicio",
        "servicio": None,
        "fecha": None,
        "fecha_texto": None,
        "hora": None,
        "nombre": None,
        "opciones": [],
        "categoria_actual": None,
        "saludado": False,
    }
    user_history[remitente] = []


def limpiar_flujo(remitente):
    saludado = user_states.get(remitente, {}).get("saludado", False)
    user_states[remitente] = {
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


def iniciar_agenda_con_servicio(state, servicio):
    state["intent"] = "agendar"
    state["step"] = "esperando_fecha"
    state["servicio"] = servicio
    state["opciones"] = []
    return f"Perfecto 😊 ¿Para qué día quieres tu {servicio['nombre']}? Puedes decir: mañana, viernes o sábado."


@app.route("/bot", methods=["POST"])
def bot():
    mensaje = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    print(f"\nMENSAJE de {remitente}: {mensaje}")

    if remitente not in user_states:
        reset_estado(remitente)
    if remitente not in user_history:
        user_history[remitente] = []

    state = user_states[remitente]
    mensaje_norm = normalizar_texto(mensaje)

    resultado_servicio = buscar_servicio(mensaje)
    servicio = resultado_servicio["data"] if resultado_servicio and resultado_servicio["tipo"] == "servicio" else None
    categoria_servicios = resultado_servicio["data"] if resultado_servicio and resultado_servicio["tipo"] == "categoria" else None
    categoria_nombre = resultado_servicio["categoria"] if resultado_servicio and resultado_servicio["tipo"] == "categoria" else None

    respuesta_texto = None

    # 1. Cancelación o humano siempre tiene prioridad.
    if any(p in mensaje_norm for p in ["cancelar", "reiniciar", "empezar de nuevo", "borrar"]):
        limpiar_flujo(remitente)
        respuesta_texto = "Claro 😊 Empecemos de nuevo. ¿Quieres revisar servicios, precios u horarios?"

    elif any(p in mensaje_norm for p in PALABRAS_HUMANO):
        respuesta_texto = "Claro 😊 Te puede contactar una persona del equipo. Déjame tu nombre y número, por favor."

    # 2. Saludo sin destruir contexto útil.
    elif any(s == mensaje_norm or mensaje_norm.startswith(s + " ") for s in ["hola", "buenas", "buenos dias", "buenas tardes", "buenas noches", "hey"]):
        if state.get("saludado"):
            respuesta_texto = "Hola de nuevo 😊 ¿Quieres consultar servicios, precios, horarios o agendar una cita?"
        else:
            state["saludado"] = True
            respuesta_texto = "¡Hola! Bienvenida a Spa Bella 🌸\nSoy Valentina. Puedo ayudarte con servicios, precios, horarios y citas.\n¿Qué estás buscando hoy?"

    # 3. Flujo activo de agendamiento.
    elif state.get("intent") == "agendar":
        if state.get("step") == "esperando_servicio":
            if servicio:
                respuesta_texto = iniciar_agenda_con_servicio(state, servicio)
            elif categoria_servicios:
                respuesta_texto = mostrar_categoria(categoria_nombre, state)
            elif quiere_servicios(mensaje):
                state["step"] = "seleccionando_categoria"
                respuesta_texto = respuesta_menu_principal()
            else:
                respuesta_texto = "¿Para qué servicio quieres la cita? Puedes responder masajes, faciales o depilación 😊"

        elif state.get("step") == "esperando_fecha":
            fecha_str, dia_semana = interpretar_fecha(mensaje)
            if fecha_str and dia_semana and dia_semana in HORARIOS_DISPONIBLES:
                state["fecha"] = fecha_str
                state["fecha_texto"] = f"{dia_semana} {fecha_str}"
                state["step"] = "esperando_hora"
                horarios = obtener_horarios_disponibles(fecha_str, dia_semana)
                if horarios:
                    respuesta_texto = f"Para el {dia_semana} tenemos estos horarios disponibles 😊\n\n{' · '.join(horarios)}\n\n¿Cuál te viene mejor?"
                else:
                    state["step"] = "esperando_fecha"
                    respuesta_texto = f"Para el {dia_semana} ya no tenemos horarios disponibles 😊 ¿Qué otro día te sirve?"
            elif dia_semana == "domingo":
                respuesta_texto = "Los domingos estamos cerrados 😊 ¿Te parece otro día de lunes a sábado?"
            else:
                respuesta_texto = "No entendí bien el día 😊 Puedes decirme: mañana, viernes o sábado."

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
                respuesta_texto = f"¡Perfecto! {hora_limpia} anotado ✨\n\n¿A nombre de quién dejo la cita?"
            else:
                horarios = " · ".join(horarios_dia) if horarios_dia else "ninguno disponible"
                respuesta_texto = f"Ese horario no está disponible 😊\nDisponibles: {horarios}\n¿Cuál prefieres?"

        elif state.get("step") == "esperando_nombre":
            nombre_cliente = mensaje.strip()
            telefono = remitente.replace("whatsapp:", "")
            try:
                cliente = buscar_o_crear_cliente(nombre_cliente, telefono)
                fecha_hora_completa = f"{state['fecha']} {state['hora']}:00"
                if horario_ocupado(fecha_hora_completa):
                    dia = state["fecha_texto"].split(" ")[0]
                    horarios = obtener_horarios_disponibles(state["fecha"], dia)
                    if horarios:
                        state["step"] = "esperando_hora"
                        respuesta_texto = f"Ese horario acaba de ocuparse 😊\nPara el {dia} queda: {' · '.join(horarios)}\n¿Cuál prefieres?"
                    else:
                        state["step"] = "esperando_fecha"
                        respuesta_texto = "Ese día ya no tiene horarios disponibles 😊 ¿Qué otro día te gustaría?"
                else:
                    cita = guardar_cita(cliente["id"], state["servicio"]["id"], fecha_hora_completa)
                    if cita:
                        dia = state["fecha_texto"].split(" ")[0]
                        respuesta_texto = f"¡Listo {nombre_cliente}! 🎉 Tu cita quedó confirmada:\n\n📋 {state['servicio']['nombre']}\n📅 {dia.capitalize()} a las {state['hora']}\n¿Necesitas algo más?"
                        limpiar_flujo(remitente)
                    else:
                        respuesta_texto = "Hubo un problema al guardar tu cita 😊 ¿Puedes intentarlo de nuevo?"
            except Exception as e:
                print(f"ERROR en agendamiento: {e}")
                respuesta_texto = "Tuve un inconveniente guardando la cita 😊 ¿Me repites tu nombre?"

    # 4. Selección de categoría desde menú principal.
    elif state.get("step") == "seleccionando_categoria":
        categoria = seleccionar_categoria(mensaje)
        if categoria:
            respuesta_texto = mostrar_categoria(categoria, state)
        else:
            respuesta_texto = f"No identifiqué la categoría 😊\n{construir_menu_categorias()}\nResponde con un número."

    # 5. Selección de servicio desde menú numerado.
    elif state.get("step") == "seleccionando_servicio" and state.get("opciones"):
        servicio_elegido = seleccionar_opcion_servicio(mensaje, state["opciones"])
        if servicio_elegido:
            respuesta_texto = respuesta_servicio_confirmado(servicio_elegido, state)
        elif quiere_servicios(mensaje):
            state["step"] = "seleccionando_categoria"
            respuesta_texto = respuesta_menu_principal()
        else:
            respuesta_texto = f"No logré identificar la opción 😊\n{construir_menu_servicios(state['opciones'])}\nResponde con el número."

    # 6. Servicio confirmado: agendar, ver opciones o responder sí/no.
    elif state.get("step") == "servicio_confirmado":
        if mensaje_norm in ["1", "si", "sí", "agendar", "quiero agendar", "cita", "reservar"] or quiere_agendar(mensaje):
            respuesta_texto = iniciar_agenda_con_servicio(state, state["servicio"])
        elif mensaje_norm in ["2", "otro", "otros", "ver otro", "servicios", "opciones"] or quiere_servicios(mensaje):
            state["step"] = "seleccionando_categoria"
            state["servicio"] = None
            respuesta_texto = respuesta_menu_principal()
        elif quiere_precio(mensaje):
            respuesta_texto = respuesta_servicio_confirmado(state["servicio"], state)
        else:
            respuesta_texto = "¿Quieres agendarlo? Responde 1 para agendar o 2 para ver otros servicios 😊"

    # 7. Comandos generales.
    elif quiere_servicios(mensaje):
        state["step"] = "seleccionando_categoria"
        respuesta_texto = respuesta_menu_principal()

    elif quiere_precio(mensaje):
        if servicio:
            respuesta_texto = respuesta_servicio_confirmado(servicio, state)
        elif categoria_servicios:
            respuesta_texto = mostrar_categoria(categoria_nombre, state)
        else:
            state["step"] = "seleccionando_categoria"
            respuesta_texto = f"Claro 😊 ¿Sobre qué categoría quieres precios?\n\n{construir_menu_categorias()}"

    elif quiere_agendar(mensaje):
        state["intent"] = "agendar"
        if servicio:
            respuesta_texto = iniciar_agenda_con_servicio(state, servicio)
        elif categoria_servicios:
            respuesta_texto = mostrar_categoria(categoria_nombre, state)
        else:
            state["step"] = "esperando_servicio"
            respuesta_texto = "Con gusto te agendo 😊 ¿Para qué servicio quieres la cita?"

    elif resultado_servicio:
        if servicio:
            respuesta_texto = respuesta_servicio_confirmado(servicio, state)
        else:
            respuesta_texto = mostrar_categoria(categoria_nombre, state)

    elif any(p in mensaje_norm for p in ["horario", "horarios", "atienden", "abierto", "abren", "cierran", "dias"]):
        respuesta_texto = "Nuestro horario es:\nLun-jue 9am-6pm, viernes 9am-8pm, sábado 9am-4pm. Domingo cerrado.\n¿Te gustaría agendar una cita?"

    elif any(p in mensaje_norm for p in ["promo", "promocion", "promociones", "descuento", "oferta"]):
        respuesta_texto = "Tenemos promociones en servicios seleccionados 😊\n¿Te interesa facial, masaje o depilación?"

    elif any(p in mensaje_norm for p in ["adios", "chao", "hasta luego", "gracias", "muchas gracias"]):
        limpiar_flujo(remitente)
        respuesta_texto = "¡Con mucho gusto! 😊 Aquí estaré si necesitas algo más de Spa Bella."

    # 8. Fuera de tema controlado.
    elif es_fuera_de_tema(mensaje):
        respuesta_texto = "Ese tema no está relacionado con Spa Bella 😊\nPuedo ayudarte con servicios, precios, horarios o citas.\n¿Quieres revisar masajes, faciales o depilación?"

    # 9. IA solo para dudas generales, con fallback seguro.
    else:
        try:
            respuesta_texto = consultar_claude(mensaje, historial=user_history[remitente])
            if not respuesta_texto or "soy valentina" in normalizar_texto(respuesta_texto):
                respuesta_texto = "Puedo ayudarte con servicios, precios, horarios o citas 😊 ¿Qué quieres revisar?"
            user_history[remitente].append({"role": "user", "content": mensaje})
            user_history[remitente].append({"role": "assistant", "content": respuesta_texto})
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
