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

OWNER_PHONE = os.getenv("OWNER_PHONE", "")

# ═══════════════════════════════════════════════════════════════
# HISTORIAL — única memoria que necesitamos
# ═══════════════════════════════════════════════════════════════
user_history = {}

# Estado simple para saber si el dueño activó el panel privado con "soy dueño".
owner_sessions = {}


# ═══════════════════════════════════════════════════════════════
# MODO DUEÑO — comandos de gestión del spa
# ═══════════════════════════════════════════════════════════════
def limpiar_telefono(numero):
    """Normaliza teléfonos para comparar OWNER_PHONE con el remitente de WhatsApp."""
    return (numero or "").replace("whatsapp:", "").replace("+", "").strip()


def es_dueno(telefono):
    """Solo devuelve True si el mensaje viene del número configurado como dueño."""
    return bool(OWNER_PHONE) and limpiar_telefono(telefono) == limpiar_telefono(OWNER_PHONE)


def menu_dueno():
    """Menú privado que se muestra cuando el dueño escribe: soy dueño."""
    return """👑 *Modo Dueño activado*

Puedes consultar respondiendo con el número o escribiendo el comando:

1️⃣ 📅 *Agenda de hoy* — citas del día
2️⃣ 📅 *Agenda de mañana* — citas de mañana
3️⃣ 📅 *Agenda del viernes* — citas de un día específico
4️⃣ 📊 *Semana* — próximas citas de 7 días
5️⃣ 💰 *Ingresos de hoy* — ventas confirmadas del día
6️⃣ 💰 *Ingresos semana* — ventas confirmadas de 7 días
7️⃣ 📌 *Resumen* — agenda + ingresos de hoy

Ejemplo: responde *1* para ver la agenda de hoy.
Escribe *salir* para volver al modo cliente 😊"""


def es_entrada_modo_dueno(mensaje):
    """Palabra/frase exacta para abrir el panel del dueño sin afectar el modo cliente."""
    msg = normalizar(mensaje)
    return any(p in msg for p in ["soy dueno", "modo dueno", "panel dueno", "panel dueño"])


def es_salida_modo_dueno(mensaje):
    """Permite volver al flujo comercial de Valentina con el mismo celular."""
    msg = normalizar(mensaje)
    return any(p in msg for p in ["salir", "salir modo dueno", "modo cliente", "cliente", "valentina"])


def es_comando_dueno(mensaje):
    """Detecta consultas internas del dueño. No se usa para clientes comunes."""
    msg = normalizar(mensaje)
    palabras_clave = [
        "agenda", "citas", "semana", "ingresos", "ventas",
        "reporte", "resumen", "comandos", "ayuda", "menu"
    ]
    return any(palabra in msg for palabra in palabras_clave)


def agenda_del_dia(fecha_str=None):
    """Devuelve las citas del día como texto formateado."""
    try:
        if not fecha_str:
            fecha_str = datetime.now().strftime("%Y-%m-%d")

        inicio = f"{fecha_str} 00:00:00"
        fin    = f"{fecha_str} 23:59:59"

        citas = supabase.table("citas") \
            .select("fecha_hora, estado, clientes(nombre), servicios(nombre)") \
            .gte("fecha_hora", inicio) \
            .lte("fecha_hora", fin) \
            .neq("estado", "cancelada") \
            .order("fecha_hora") \
            .execute()

        if not citas.data:
            return f"📅 No hay citas agendadas para el {fecha_str}."

        # Formato de fecha legible
        fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d")
        dias = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
        meses = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
        dia_nombre = dias[fecha_dt.weekday()]
        fecha_legible = f"{dia_nombre} {fecha_dt.day} de {meses[fecha_dt.month-1]}"

        lineas = [f"📅 *Agenda — {fecha_legible}*\n"]
        for c in citas.data:
            hora  = str(c["fecha_hora"])[11:16]
            nombre_cliente  = c.get("clientes", {}).get("nombre", "Sin nombre")
            nombre_servicio = c.get("servicios", {}).get("nombre", "Sin servicio")
            estado = "✅" if c["estado"] == "confirmada" else "⏳"
            lineas.append(f"{estado} {hora} — {nombre_cliente} — {nombre_servicio}")

        lineas.append(f"\nTotal: {len(citas.data)} cita(s)")
        return "\n".join(lineas)

    except Exception as e:
        print(f"ERROR agenda: {e}")
        return "No pude consultar la agenda en este momento 😊"


def resumen_semana():
    """Citas de los próximos 7 días."""
    try:
        hoy = datetime.now()
        inicio = hoy.strftime("%Y-%m-%d") + " 00:00:00"
        fin    = (hoy + timedelta(days=7)).strftime("%Y-%m-%d") + " 23:59:59"

        citas = supabase.table("citas") \
            .select("fecha_hora, clientes(nombre), servicios(nombre)") \
            .gte("fecha_hora", inicio) \
            .lte("fecha_hora", fin) \
            .neq("estado", "cancelada") \
            .order("fecha_hora") \
            .execute()

        if not citas.data:
            return "📊 No hay citas en los próximos 7 días."

        lineas = [f"📊 *Próximas citas (7 días)* — {len(citas.data)} en total\n"]
        for c in citas.data:
            fecha = str(c["fecha_hora"])[:10]
            hora  = str(c["fecha_hora"])[11:16]
            nombre  = c.get("clientes", {}).get("nombre", "Sin nombre")
            servicio = c.get("servicios", {}).get("nombre", "Sin servicio")
            lineas.append(f"📌 {fecha} {hora} — {nombre} — {servicio}")

        return "\n".join(lineas)

    except Exception as e:
        print(f"ERROR semana: {e}")
        return "No pude consultar la agenda semanal 😊"


def ingresos_periodo(dias=0):
    """Calcula ingresos estimados según citas confirmadas.
    dias=0 consulta solo hoy. dias=7 consulta desde hoy hasta 7 días.
    """
    try:
        hoy = datetime.now()
        fecha_inicio = hoy.strftime("%Y-%m-%d")
        fecha_fin = (hoy + timedelta(days=dias)).strftime("%Y-%m-%d")

        inicio = f"{fecha_inicio} 00:00:00"
        fin    = f"{fecha_fin} 23:59:59"

        citas = supabase.table("citas") \
            .select("fecha_hora, estado, servicios(nombre, precio)") \
            .gte("fecha_hora", inicio) \
            .lte("fecha_hora", fin) \
            .eq("estado", "confirmada") \
            .order("fecha_hora") \
            .execute()

        if not citas.data:
            periodo = "hoy" if dias == 0 else "los próximos 7 días"
            return f"💰 No hay ingresos confirmados para {periodo}."

        total = 0
        lineas = []
        for c in citas.data:
            servicio = c.get("servicios") or {}
            nombre_servicio = servicio.get("nombre", "Servicio")
            precio = servicio.get("precio", 0) or 0
            try:
                total += float(precio)
            except Exception:
                pass

            fecha = str(c["fecha_hora"])[:10]
            hora  = str(c["fecha_hora"])[11:16]
            lineas.append(f"• {fecha} {hora} — {nombre_servicio} — {formatear_precio(precio)}")

        titulo = "💰 *Ingresos de hoy*" if dias == 0 else "💰 *Ingresos próximos 7 días*"
        return "\n".join([
            titulo,
            f"Total estimado: *{formatear_precio(total)}*",
            f"Citas confirmadas: {len(citas.data)}",
            "",
            *lineas
        ])

    except Exception as e:
        print(f"ERROR ingresos: {e}")
        return "No pude consultar los ingresos en este momento 😊"


def resumen_dueno():
    """Resumen rápido para demostración: citas e ingresos de hoy."""
    return f"{agenda_del_dia()}\n\n────────────\n\n{ingresos_periodo(0)}"


def procesar_comando_dueno(mensaje):
    """Procesa comandos del dueño y devuelve respuesta."""
    msg = normalizar(mensaje)

    # También acepta números del menú privado.
    if msg in ["1", "01", "uno"]:
        return agenda_del_dia()

    if msg in ["2", "02", "dos"]:
        manana = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return agenda_del_dia(manana)

    if msg in ["3", "03", "tres"]:
        hoy = datetime.now()
        dias_semana_idx = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        diff = (dias_semana_idx.index("viernes") - hoy.weekday()) % 7 or 7
        fecha = (hoy + timedelta(days=diff)).strftime("%Y-%m-%d")
        return agenda_del_dia(fecha)

    if msg in ["4", "04", "cuatro"]:
        return resumen_semana()

    if msg in ["5", "05", "cinco"]:
        return ingresos_periodo(0)

    if msg in ["6", "06", "seis"]:
        return ingresos_periodo(7)

    if msg in ["7", "07", "siete"]:
        return resumen_dueno()

    # Agenda de hoy
    if any(p in msg for p in ["agenda de hoy", "agenda hoy", "citas de hoy", "que hay hoy"]):
        return agenda_del_dia()

    # Agenda de mañana
    if any(p in msg for p in ["agenda de manana", "agenda manana", "citas de manana"]):
        manana = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return agenda_del_dia(manana)

    # Agenda de un día específico
    if "agenda del" in msg or "citas del" in msg or "agenda" in msg or "citas" in msg:
        dias_semana_idx = ["lunes","martes","miercoles","jueves","viernes","sabado","domingo"]
        hoy = datetime.now()
        for dia in dias_semana_idx:
            if dia in msg:
                diff = (dias_semana_idx.index(dia) - hoy.weekday()) % 7 or 7
                fecha = (hoy + timedelta(days=diff)).strftime("%Y-%m-%d")
                return agenda_del_dia(fecha)

    # Resumen semanal
    if any(p in msg for p in ["semana", "esta semana", "proximas citas", "próximas citas"]):
        return resumen_semana()

    # Ingresos
    if any(p in msg for p in ["ingresos de hoy", "ventas de hoy", "ingreso hoy", "venta hoy"]):
        return ingresos_periodo(0)

    if any(p in msg for p in ["ingresos semana", "ingresos de la semana", "ventas semana", "ventas de la semana"]):
        return ingresos_periodo(7)

    # Resumen rápido
    if any(p in msg for p in ["resumen", "reporte", "dashboard", "panel"]):
        return resumen_dueno()

    # Menú / ayuda
    if es_entrada_modo_dueno(msg) or any(p in msg for p in ["ayuda", "comandos", "que puedes hacer", "menu"]):
        return menu_dueno()

    return menu_dueno()  # Si llegó al modo dueño pero no entendió, mostramos opciones

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

    # ── MODO DUEÑO ACTIVADO CON PALABRA CLAVE ──────────────────
    # Esto permite usar UN SOLO CELULAR para la demo:
    # - Si escribes normal: entra Valentina como cliente.
    # - Si escribes "soy dueño": se abre el panel privado.
    # - Dentro del panel puedes consultar agenda, citas, ingresos y resumen.
    # - Para volver a Valentina: escribe "salir" o "modo cliente".
    if es_dueno(telefono):
        if es_entrada_modo_dueno(mensaje):
            owner_sessions[remitente] = True
            respuesta_texto = menu_dueno()
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            print(f"👑 DUEÑO ACTIVADO: {respuesta_texto[:80]}...")
            return Response(str(resp), mimetype="application/xml")

        if owner_sessions.get(remitente):
            if es_salida_modo_dueno(mensaje):
                owner_sessions[remitente] = False
                respuesta_texto = "Listo 😊 Volvemos al modo cliente. Escríbeme como clienta y te atiendo como Valentina 🌸"
            else:
                respuesta_texto = procesar_comando_dueno(mensaje)

            resp = MessagingResponse()
            resp.message(respuesta_texto)
            print(f"👑 DUEÑO: {respuesta_texto[:80]}...")
            return Response(str(resp), mimetype="application/xml")

    # ── MODO CLIENTE ───────────────────────────────────────────
    if remitente not in user_history:
        user_history[remitente] = []

    historial = user_history[remitente]

    try:
        respuesta_texto, msg_usuario = responder(mensaje, historial, telefono)
    except Exception as e:
        print(f"❌ ERROR: {e}")
        respuesta_texto = "Tuve un inconveniente 😊 ¿Puedes repetirme tu consulta?"
        msg_usuario = mensaje

    # Guardamos solo texto plano en el historial
    user_history[remitente] = (historial + [
        {"role": "user",      "content": msg_usuario},
        {"role": "assistant", "content": respuesta_texto},
    ])[-24:]

    print(f"📤 VALENTINA: {respuesta_texto}")

    resp = MessagingResponse()
    resp.message(respuesta_texto)
    return Response(str(resp), mimetype="application/xml")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
