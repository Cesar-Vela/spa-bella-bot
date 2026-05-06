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

# ═══════════════════════════════════════════════════════════════
# PERSONALIDAD DE VALENTINA - Prompt que da vida al bot
# ═══════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """
Eres Valentina, recepcionista y asesora de bienestar del Spa Bella. 
Atiendes por WhatsApp como una persona real, cálida, profesional y con experiencia en spa.

TU ESTILO DE RESPUESTA:
- Eres conversacional, no robótica. Usa un tono amigable como si estuvieras en la recepción del spa.
- Máximo 4 líneas salvo cuando listes opciones o expliques servicios.
- Usa emojis con moderación (🌸 💆‍♀️ ✨ 😊).
- NUNCA digas que eres IA, bot o asistente virtual. Eres Valentina del Spa Bella.
- Siempre termina ofreciendo algo más: agendar, ver precios, conocer otro servicio.

REGLAS DE NEGOCIO:
- NO inventes precios, promociones ni servicios que no existan.
- Si te preguntan algo FUERA del spa (política, noticias, chistes, matemáticas, etc.), 
  responde con empatía pero REDIRIGE suavemente: "Eso no lo manejo desde acá 😊 pero dime, 
  ¿buscas relajarte hoy? Te puedo contar de nuestros masajes o faciales."
- Servicios disponibles: masajes, faciales, depilación, tratamientos corporales, uñas, pestañas, cabello.
- NO ofreces: botox, ácido hialurónico, cirugías, láser médico.
- Dirección: Av. Principal 456, Col. Centro, justo frente al parque 📍
- Horario: Lun-Jue 9am-6pm, Vie 9am-8pm, Sáb 9am-4pm. Domingo cerrado.

OBJETIVO PRINCIPAL:
Entender qué busca el cliente, recomendarle lo mejor del spa, y llevarlo naturalmente 
a consultar precio o agendar una cita. Nunca dejes la conversación sin una invitación a la acción.
"""

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE HORARIOS Y SERVICIOS
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

CATEGORIAS = {
    "masajes": ["masaje", "masajes", "relajacion", "dolor muscular", "espalda", "descanso", "estres"],
    "faciales": ["facial", "faciales", "cara", "rostro", "piel", "cutis"],
    "depilacion": ["depilacion", "depilar", "vello", "cera", "depilarme"],
    "corporales": ["reductivo", "corporal", "cuerpo", "medidas", "silueta", "abdomen", "cintura"],
    "unas": ["manicure", "pedicure", "unas", "uñas", "manos", "pies"],
    "pestanas": ["pestanas", "pestañas", "cejas", "mirada", "lifting"],
    "cabello": ["keratina", "tinte", "corte", "cabello", "pelo", "alisado"],
}

# Descripciones ricas para que Valentina explique como experta
DESCRIPCIONES_SERVICIOS = {
    "masaje relajante": "Ideal si quieres desconectarte por completo. Trabajamos con movimientos suaves y aromaterapia para bajar el estrés y soltar tensión acumulada.",
    "masaje descontracturante": "Perfecto cuando sientes la espalda cargada, nudos o tensión muscular. Usamos presión media-alta para liberar esas contracturas profundas.",
    "masaje con piedras calientes": "Una experiencia súper relajante. El calor de las piedras volcánicas penetra en la musculatura y ayuda a soltar tensiones que el masaje normal no alcanza.",
    "facial hidratante": "Devuelve la luminosidad a tu piel. Incluye limpieza profunda, exfoliación suave e hidratación intensiva. Sale radiante ✨",
    "facial anti-edad": "Tratamiento más completo para pieles maduras. Trabajamos con técnicas de lifting manual y productos que mejoran la firmeza del rostro.",
    "depilacion de axilas": "Rápido y práctico. Usamos cera de alta calidad que deja la piel suave sin irritación.",
    "depilacion de piernas": "Piernas completas, de tobillo a ingles. La piel queda súper suave y limpia.",
    "depilacion piernas completas": "Piernas completas, de tobillo a ingles. La piel queda súper suave y limpia.",
    "depilacion de ingles": "Depilación de zona bikini con cera tibia, realizada con mucha discreción y cuidado.",
    "tratamiento reductivo": "Trabajamos zonas localizadas (abdomen, cintura, piernas) con técnicas manuales y productos reductores. Es un complemento ideal a tus hábitos.",
    "diseno de pestanas": "Incluye lifting, tinte y arreglo de cejas para resaltar tu mirada naturalmente.",
    "manicure clasico": "Limpieza, cutícula, forma y esmaltado tradicional. Uñas arregladas y presentables.",
    "manicure semipermanente": "Esmaltado que dura hasta 3 semanas sin descascararse. Ideal si no tienes tiempo para retocar constantemente.",
    "keratina": "Tratamiento capilar que controla el frizz y suaviza el cabello por semanas. Sale con brillo y manejable.",
    "tinte + corte": "Renovación completa: color según tu tono de piel y corte que favorece tu tipo de rostro.",
}

RECOMENDACIONES_CATEGORIA = {
    "masajes": "Si buscas descanso total, te recomiendo el relajante con aromaterapia. Si tienes espalda cargada o dolores, el descontracturante es tu mejor opción. ¿Cuál te llama más?",
    "faciales": "Para piel seca o opaca, el hidratante devuelve la luminosidad. Si buscas cuidado anti-edad, tenemos el facial reafirmante. ¿Te gustaría saber precios?",
    "depilacion": "La más pedida es piernas completas + axilas. Pero si quieres empezar suave, las axilas solas son rápidas y prácticas. ¿Qué zona te interesa?",
    "corporales": "El reductivo es ideal si quieres trabajar abdomen o cintura. Lo combinamos con drenaje para mejores resultados. ¿Te gustaría agendar una evaluación?",
    "unas": "El semipermanente es el favorito de las clientas porque dura semanas intacto. Pero si prefieres cambiar de color seguido, el clásico es más versátil.",
    "pestanas": "El diseño completo incluye lifting + tinte + cejas. Es el más completo y queda espectacular. ¿Te animas a probarlo?",
    "cabello": "La keratina es perfecta para controlar frizz. Si quieres cambio de look, el tinte + corte incluye asesoría de color. ¿Cuál te interesa más?",
}

TEMAS_FUERA = [
    "maduro", "presidente", "politica", "política", "gobierno", "elecciones",
    "futbol", "fútbol", "noticias", "religion", "religión", "dolar", "dólar",
    "clima", "pais", "país", "venezuela", "colombia", "trump", "petro", "bitcoin",
    "acciones", "bolsa", "guerra", "crimen", "accidente", "tiktok", "instagram"
]

PALABRAS_HUMANO = ["humano", "asesor", "asesora", "persona", "llamar", "me llaman", "hablar con alguien", "queja", "reclamo", "gerente"]

SERVICIOS_NO_DISPONIBLES = [
    "botox", "toxina", "acido hialuronico", "hialuronico", "relleno", "labios",
    "rinomodelacion", "micropigmentacion", "laser", "lipo", "cirugia", "cirugía",
    "plasma", "mesoterapia", "acido", "injeccion", "inyección"
]

INFO_NEGOCIO = {
    "nombre": "Spa Bella",
    "direccion": "Av. Principal 456, Col. Centro, justo frente al parque",
    "horario_corto": "lunes a jueves 9am-6pm, viernes 9am-8pm y sábado 9am-4pm",
    "domingos": "Los domingos estamos cerrados.",
}

# ═══════════════════════════════════════════════════════════════
# FUNCIONES AUXILIARES
# ═══════════════════════════════════════════════════════════════
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

def obtener_servicios():
    resultado = supabase.table("servicios").select("*").execute()
    return resultado.data or []

def servicio_pertenece_categoria(servicio, categoria):
    categoria_db = normalizar_texto(servicio.get("categoria", ""))
    if categoria_db and categoria_db == normalizar_texto(categoria):
        return True

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

def descripcion_servicio(nombre):
    return DESCRIPCIONES_SERVICIOS.get(normalizar_texto(nombre), "Es uno de nuestros servicios más solicitados en Spa Bella. ¿Te gustaría probarlo?")

def buscar_servicio(mensaje):
    data = obtener_servicios()
    mensaje_norm = normalizar_texto(mensaje)

    # Match exacto
    for servicio in data:
        nombre_norm = normalizar_texto(servicio["nombre"])
        if nombre_norm in mensaje_norm or mensaje_norm in nombre_norm:
            return {"tipo": "servicio", "data": servicio}

    # Match por palabras clave
    PALABRAS_CLAVE = {
        "masaje relajante": ["relajante", "relajacion", "relajarme", "estres", "descansar", "relajar", "suave"],
        "masaje descontracturante": ["descontracturante", "contractura", "dolor", "espalda", "nudos", "tension", "cargada"],
        "masaje con piedras calientes": ["piedras", "calientes", "piedras calientes", "calor", "volcanicas"],
        "facial hidratante": ["hidratante", "hidratacion", "piel seca", "hidratarme", "hidrata", "luminosidad"],
        "facial anti-edad": ["anti edad", "antiedad", "arrugas", "rejuvenecer", "anti-edad", "reafirmante"],
        "depilacion de axilas": ["axila", "axilas"],
        "depilacion de piernas": ["pierna", "piernas", "completa", "completas"],
        "depilacion de ingles": ["ingles", "bikini"],
        "tratamiento reductivo": ["reductivo", "reducir", "adelgazar", "medidas", "corporal", "abdomen", "cintura"],
        "diseno de pestanas": ["pestanas", "pestanas", "cejas", "lifting", "mirada"],
        "manicure clasico": ["manicure", "clasico", "unas", "uñas", "esmaltado"],
        "manicure semipermanente": ["semipermanente", "semi permanente", "gel"],
        "keratina": ["keratina", "alisado", "frizz"],
        "tinte + corte": ["tinte", "corte", "cabello", "pelo", "color"],
    }

    for servicio in data:
        nombre_norm = normalizar_texto(servicio["nombre"])
        for nombre_servicio, keywords in PALABRAS_CLAVE.items():
            if normalizar_texto(nombre_servicio) == nombre_norm:
                for palabra in keywords:
                    if normalizar_texto(palabra) in mensaje_norm:
                        return {"tipo": "servicio", "data": servicio}

    # Match por categoría
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

# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE AGENDAMIENTO
# ═══════════════════════════════════════════════════════════════
def buscar_o_crear_cliente(nombre, telefono):
    resultado = supabase.table("clientes").select("*").eq("telefono", telefono).execute()
    if resultado.data:
        cliente = resultado.data[0]
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
            "estado": "confirmada",
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
        print(f"ERROR consultando horario: {e}")
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
        print(f"ERROR obteniendo horarios: {e}")
        return []

def interpretar_fecha(texto):
    texto_norm = normalizar_texto(texto)
    hoy = datetime.now()
    dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

    if "hoy" in texto_norm:
        return hoy.strftime("%Y-%m-%d"), DIAS_ES.get(hoy.strftime("%A").lower(), "")
    if "manana" in texto_norm or "mañana" in texto:
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

# ═══════════════════════════════════════════════════════════════
# FUNCIONES DE ESTADO
# ═══════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════
# RESPUESTAS CON PERSONALIDAD DE VALENTINA
# ═══════════════════════════════════════════════════════════════
def respuesta_menu_principal():
    return f"""¿Qué te gustaría revisar? 😊

{construir_menu_categorias()}

Responde con el número o el nombre."""

def mostrar_categoria(categoria, state):
    servicios = servicios_por_categoria(categoria)
    if not servicios:
        state["step"] = "inicio"
        return "Por ahora no tengo opciones en esa categoría 😊 ¿Te interesa revisar masajes, faciales o depilación?"

    state["step"] = "seleccionando_servicio"
    state["opciones"] = servicios
    state["categoria_actual"] = categoria
    etiqueta = CATEGORIA_LABELS.get(categoria, categoria)
    recomendacion = RECOMENDACIONES_CATEGORIA.get(categoria, "")
    
    return f"""Tenemos estas opciones de {etiqueta} ✨

{construir_menu_servicios(servicios)}

{recomendacion}

Responde con el número de la opción que te interese."""

def respuesta_servicio_confirmado(servicio, state):
    state["step"] = "servicio_confirmado"
    state["intent"] = "servicio"
    state["servicio"] = servicio
    nombre = servicio["nombre"]
    precio = formatear_precio(servicio["precio"])
    desc = servicio.get("descripcion") or descripcion_servicio(nombre)
    
    return f"""{nombre} cuesta {precio} 😊

{desc}

¿Te gustaría agendarlo? Responde:
1️⃣ Sí, quiero agendar
2️⃣ Ver otros servicios"""

def iniciar_agenda_con_servicio(state, servicio):
    state["intent"] = "agendar"
    state["step"] = "esperando_fecha"
    state["servicio"] = servicio
    state["opciones"] = []
    return f"Perfecto 😊 ¿Para qué día quieres tu {servicio['nombre']}? Puedes decirme: mañana, viernes, sábado... o una fecha como 2025-05-15."

def respuesta_fuera_tema():
    return """Eso no lo manejo desde acá 😊 

Pero dime, ¿buscas relajarte hoy? Te puedo contar de nuestros masajes, faciales o ayudarte a agendar una cita. ¿Qué te interesa?"""

def respuesta_servicio_no_disponible():
    return """Por ahora no ofrecemos ese servicio en Spa Bella 😊

Lo que sí manejamos y queda espectacular:
• Masajes relajantes y descontracturantes
• Faciales hidratantes y anti-edad
• Depilación con cera
• Tratamientos corporales reductivos

¿Te gustaría que te cuente de alguno?"""

def respuesta_direccion():
    return f"""Estamos en {INFO_NEGOCIO['direccion']} 📍

Atendemos {INFO_NEGOCIO['horario_corto']}.
{INFO_NEGOCIO['domingos']}

¿Te gustaría que te ayude a agendar una cita?"""

def respuesta_horarios():
    return f"""Nuestro horario es:
• Lun-Jue: 9:00 AM - 6:00 PM
• Viernes: 9:00 AM - 8:00 PM  
• Sábado: 9:00 AM - 4:00 PM
• Domingo: Cerrado

¿Qué día te viene mejor para agendar? 😊"""

def respuesta_promociones():
    return """Tenemos promociones en servicios seleccionados ✨

¿Te interesa alguna de estas áreas?
• Faciales (hidrante o anti-edad)
• Masajes (relajante o descontracturante)
• Depilación (axilas, piernas, bikini)
• Corporales (reductivos)

Dime cuál te llama y te cuento lo que tenemos 😊"""

# ═══════════════════════════════════════════════════════════════
# CLAUDE CON CONTROL DE CONTEXTO
# ═══════════════════════════════════════════════════════════════
def consultar_claude(mensaje, historial=[]):
    mensajes = []
    for h in historial[-6:]:
        mensajes.append(h)
    mensajes.append({"role": "user", "content": mensaje})
    
    respuesta = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=150,
        temperature=0.4,
        system=SYSTEM_PROMPT,
        messages=mensajes,
    )
    return respuesta.content[0].text.strip()

# ═══════════════════════════════════════════════════════════════
# DETECTORES DE INTENCIÓN
# ═══════════════════════════════════════════════════════════════
def es_fuera_de_tema(mensaje):
    msg = normalizar_texto(mensaje)
    return any(normalizar_texto(p) in msg for p in TEMAS_FUERA)

def quiere_servicios(mensaje):
    msg = normalizar_texto(mensaje)
    patrones = ["servicio", "servicios", "opciones", "todo", "todas", "que tienen", "que ofrecen", "tratamientos", "menu", "menú", "catalogo"]
    return any(p in msg for p in patrones)

def quiere_precio(mensaje):
    msg = normalizar_texto(mensaje)
    patrones = ["precio", "precios", "valor", "cuanto", "cuesta", "cuestan", "tarifa", "vale", "vale la"]
    return any(p in msg for p in patrones)

def quiere_agendar(mensaje):
    msg = normalizar_texto(mensaje)
    patrones = ["cita", "agendar", "reservar", "turno", "quiero agendar", "agenda", "hora", "programar", "apartar"]
    return any(p in msg for p in patrones)

def quiere_direccion(mensaje):
    msg = normalizar_texto(mensaje)
    patrones = ["direccion", "ubicacion", "donde quedan", "donde estan", "como llego", "donde es", "sede", "mapa", "llegar"]
    return any(p in msg for p in patrones)

def es_servicio_no_disponible(mensaje):
    msg = normalizar_texto(mensaje)
    return any(normalizar_texto(p) in msg for p in SERVICIOS_NO_DISPONIBLES)

def es_saludo(mensaje):
    msg = normalizar_texto(mensaje)
    saludos = ["hola", "buenas", "buenos dias", "buenos días", "buenas tardes", "buenas noches", "hey", "hi", "hello", "que tal", "como estas"]
    return any(s in msg for s in saludos)

def es_despedida(mensaje):
    msg = normalizar_texto(mensaje)
    return any(p in msg for p in ["adios", "adiós", "chao", "hasta luego", "nos vemos", "bye", "gracias", "muchas gracias", "gracias por todo"])

def es_cancelar(mensaje):
    msg = normalizar_texto(mensaje)
    return any(p in msg for p in ["cancelar", "reiniciar", "empezar de nuevo", "borrar", "olvidar", "desde cero", "otra cosa", "cambiar"])

def es_humano(mensaje):
    msg = normalizar_texto(mensaje)
    return any(p in msg for p in PALABRAS_HUMANO)

# ═══════════════════════════════════════════════════════════════
# ENDPOINT PRINCIPAL
# ═══════════════════════════════════════════════════════════════
@app.route("/bot", methods=["POST"])
def bot():
    mensaje = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    print(f"\n📩 MENSAJE de {remitente}: {mensaje}")

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

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 1: Cancelar o hablar con humano
    # ═══════════════════════════════════════════════════════════
    if es_cancelar(mensaje):
        limpiar_flujo(remitente)
        respuesta_texto = "Claro 😊 Empecemos de nuevo. ¿Quieres revisar servicios, precios u horarios?"

    elif es_humano(mensaje):
        respuesta_texto = "Entiendo, quieres hablar con alguien del equipo 😊 Déjame tu nombre y un horario en el que te podamos llamar. Te contactamos pronto."

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 2: Saludos (sin destruir contexto)
    # ═══════════════════════════════════════════════════════════
    elif es_saludo(mensaje):
        if state.get("saludado") and state.get("step") != "inicio":
            respuesta_texto = "Hola de nuevo 😊 ¿En qué más puedo ayudarte? Recuerda que puedo agendarte o contarte de nuestros servicios."
        else:
            state["saludado"] = True
            respuesta_texto = "¡Hola! Bienvenida a Spa Bella 🌸\n\nSoy Valentina. Te ayudo con servicios, precios, horarios y citas.\n\n¿Qué estás buscando hoy? ¿Relajación, un facial, depilación o algo especial?"

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 3: Comandos generales (funcionan en cualquier momento)
    # ═══════════════════════════════════════════════════════════
    elif quiere_direccion(mensaje):
        respuesta_texto = respuesta_direccion()

    elif any(p in mensaje_norm for p in ["horario", "horarios", "atienden", "abierto", "abren", "cierran", "dias", "días", "que dia", "hasta que hora"]):
        respuesta_texto = respuesta_horarios()

    elif any(p in mensaje_norm for p in ["promo", "promocion", "promociones", "descuento", "oferta", "ofertas", "promo", "descuentos"]):
        respuesta_texto = respuesta_promociones()

    elif es_servicio_no_disponible(mensaje):
        respuesta_texto = respuesta_servicio_no_disponible()

    elif es_fuera_de_tema(mensaje):
        respuesta_texto = respuesta_fuera_tema()

    elif quiere_servicios(mensaje) and state.get("intent") != "agendar":
        state["step"] = "seleccionando_categoria"
        respuesta_texto = respuesta_menu_principal()

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 4: FLUJO DE AGENDAMIENTO
    # ═══════════════════════════════════════════════════════════
    elif state.get("intent") == "agendar":
        # Esperando servicio
        if state.get("step") == "esperando_servicio":
            if servicio:
                respuesta_texto = iniciar_agenda_con_servicio(state, servicio)
            elif categoria_servicios:
                respuesta_texto = mostrar_categoria(categoria_nombre, state)
            elif quiere_servicios(mensaje):
                state["step"] = "seleccionando_categoria"
                respuesta_texto = respuesta_menu_principal()
            else:
                respuesta_texto = "¿Para qué servicio quieres la cita? Puedes decirme masajes, faciales, depilación... o escribe 'servicios' para ver todo 😊"

        # Esperando fecha
        elif state.get("step") == "esperando_fecha":
            fecha_str, dia_semana = interpretar_fecha(mensaje)
            if fecha_str and dia_semana and dia_semana in HORARIOS_DISPONIBLES:
                state["fecha"] = fecha_str
                state["fecha_texto"] = f"{dia_semana} {fecha_str}"
                state["step"] = "esperando_hora"
                horarios = obtener_horarios_disponibles(fecha_str, dia_semana)
                if horarios:
                    lista = " · ".join(horarios)
                    respuesta_texto = f"Para el {dia_semana} tenemos estos horarios 😊\n\n{lista}\n\n¿Cuál te viene mejor?"
                else:
                    state["step"] = "esperando_fecha"
                    respuesta_texto = f"Para el {dia_semana} ya no tenemos cupos 😊 ¿Qué otro día te sirve? Puedes decirme mañana, viernes..."
            elif dia_semana == "domingo":
                respuesta_texto = "Los domingos estamos cerrados 😊 ¿Te parece otro día de lunes a sábado?"
            else:
                respuesta_texto = "No entendí bien el día 😊 Puedes decirme: mañana, viernes, sábado, o una fecha como 2025-05-15."

        # Esperando hora
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
                respuesta_texto = f"Ese horario ya no está disponible 😊\nDisponibles: {horarios}\n¿Cuál prefieres?"

        # Esperando nombre y confirmar
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
                        respuesta_texto = "Ese día ya no tiene horarios 😊 ¿Qué otro día te gustaría?"
                else:
                    cita = guardar_cita(cliente["id"], state["servicio"]["id"], fecha_hora_completa)
                    if cita:
                        dia = state["fecha_texto"].split(" ")[0]
                        nombre_serv = state["servicio"]["nombre"]
                        respuesta_texto = f"""¡Listo {nombre_cliente}! 🎉 Tu cita quedó confirmada:

📋 {nombre_serv}
📅 {dia.capitalize()} a las {state['hora']}
📍 Av. Principal 456, Col. Centro

Te esperamos 😊 ¿Necesitas algo más?"""
                        limpiar_flujo(remitente)
                    else:
                        respuesta_texto = "Tuve un problema guardando la cita 😊 ¿Puedes repetirme tu nombre?"
            except Exception as e:
                print(f"ERROR en agendamiento: {e}")
                respuesta_texto = "Tuve un inconveniente 😊 ¿Me repites tu nombre para confirmar?"

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 5: SELECCIÓN DE CATEGORÍA
    # ═══════════════════════════════════════════════════════════
    elif state.get("step") == "seleccionando_categoria":
        categoria = seleccionar_categoria(mensaje)
        if categoria:
            respuesta_texto = mostrar_categoria(categoria, state)
        else:
            respuesta_texto = f"No identifiqué la categoría 😊\n{construir_menu_categorias()}\nResponde con un número o el nombre."

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 6: SELECCIÓN DE SERVICIO ESPECÍFICO
    # ═══════════════════════════════════════════════════════════
    elif state.get("step") == "seleccionando_servicio" and state.get("opciones"):
        # Si el cliente cambia de tema o menciona otro servicio/categoría, no lo dejamos atrapado en el menú anterior.
        if quiere_servicios(mensaje) and not mensaje_norm.isdigit():
            state["step"] = "seleccionando_categoria"
            respuesta_texto = respuesta_menu_principal()
        elif servicio:
            respuesta_texto = respuesta_servicio_confirmado(servicio, state)
        elif categoria_servicios:
            respuesta_texto = mostrar_categoria(categoria_nombre, state)
        else:
            servicio_elegido = seleccionar_opcion_servicio(mensaje, state["opciones"])
            if servicio_elegido:
                respuesta_texto = respuesta_servicio_confirmado(servicio_elegido, state)
            else:
                categoria = seleccionar_categoria(mensaje)
                if categoria:
                    respuesta_texto = mostrar_categoria(categoria, state)
                else:
                    respuesta_texto = f"No logré identificar la opción 😊\n{construir_menu_servicios(state['opciones'])}\nResponde con el número o escribe 'servicios' para volver al menú."

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 7: SERVICIO CONFIRMADO - AGENDAR O VER MÁS
    # ═══════════════════════════════════════════════════════════
    elif state.get("step") == "servicio_confirmado":
        if servicio and state.get("servicio") and servicio.get("id") != state["servicio"].get("id"):
            respuesta_texto = respuesta_servicio_confirmado(servicio, state)
        elif categoria_servicios:
            respuesta_texto = mostrar_categoria(categoria_nombre, state)
        elif mensaje_norm in ["1", "si", "sí", "agendar", "quiero agendar", "cita", "reservar", "dale", "ok"] or quiere_agendar(mensaje):
            respuesta_texto = iniciar_agenda_con_servicio(state, state["servicio"])
        elif mensaje_norm in ["2", "otro", "otros", "ver otro", "servicios", "opciones", "no", "mas"] or quiere_servicios(mensaje):
            state["step"] = "seleccionando_categoria"
            state["servicio"] = None
            respuesta_texto = respuesta_menu_principal()
        elif quiere_precio(mensaje):
            respuesta_texto = respuesta_servicio_confirmado(state["servicio"], state)
        else:
            respuesta_texto = "¿Te gustaría agendarlo? Responde 1 para agendar o 2 para ver otros servicios 😊"

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 8: COMANDOS GENERALES SIN ESTADO ACTIVO
    # ═══════════════════════════════════════════════════════════
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
            respuesta_texto = "Con gusto te agendo 😊 ¿Para qué servicio quieres la cita? Dime masajes, faciales, depilación..."

    elif resultado_servicio:
        if servicio:
            respuesta_texto = respuesta_servicio_confirmado(servicio, state)
        else:
            respuesta_texto = mostrar_categoria(categoria_nombre, state)

    elif state.get("step") == "inicio" and mensaje_norm in ["si", "sí", "ok", "dale", "1", "quiero", "claro"]:
        respuesta_texto = "Claro 😊 ¿quieres agendar otra cita o prefieres revisar servicios y precios?\n\n" + construir_menu_categorias()

    elif es_despedida(mensaje):
        limpiar_flujo(remitente)
        respuesta_texto = "¡Con mucho gusto! 😊✨ Aquí estaré si necesitas algo más de Spa Bella. ¡Que tengas un lindo día!"

    # ═══════════════════════════════════════════════════════════
    # PRIORIDAD 9: CLAUDE CON PERSONALIDAD DE VALENTINA
    # ═══════════════════════════════════════════════════════════
    else:
        try:
            respuesta_texto = consultar_claude(mensaje, historial=user_history[remitente])
            if not respuesta_texto:
                respuesta_texto = "Puedo ayudarte con servicios, precios, horarios o citas 😊 ¿Qué te interesa revisar?"
            
            user_history[remitente].append({"role": "user", "content": mensaje})
            user_history[remitente].append({"role": "assistant", "content": respuesta_texto})
            user_history[remitente] = user_history[remitente][-20:]
            print(f"🤖 VALENTINA: {respuesta_texto}")
        except Exception as e:
            print(f"❌ ERROR CLAUDE: {e}")
            respuesta_texto = "No logré entenderte bien 😊 ¿Quieres que te cuente de nuestros masajes, faciales o te ayude a agendar?"

    # ═══════════════════════════════════════════════════════════
    # ENVIAR RESPUESTA
    # ═══════════════════════════════════════════════════════════
    respuesta = MessagingResponse()
    respuesta.message(respuesta_texto)
    print(f"📤 ENVIANDO: {str(respuesta)}")
    return Response(str(respuesta), mimetype="application/xml")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))