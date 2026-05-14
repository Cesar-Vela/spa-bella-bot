import os
import json
import re
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

# Modo demo: útil para presentaciones desde un solo WhatsApp.
# En producción real déjalo en false o no configures esta variable.
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() in ["1", "true", "yes", "si", "sí"]
demo_sessions = {}


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
8️⃣ 🔎 *Buscar cliente/cita* — ejemplo: buscar Oscar
9️⃣ 🧾 *Historial de cambios* — últimas cancelaciones/reagendadas
🔒 *Bloquear horario* — ejemplo: bloquear viernes 15:00 reunión personal
🔓 *Liberar horario* — ejemplo: liberar viernes 15:00
📋 *Ver bloqueos* — muestra horarios bloqueados activos
0️⃣ 🚪 *Salir* — volver al modo cliente

Ejemplo: responde *1* para ver la agenda de hoy.
También puedes escribir: *buscar Oscar*, *historial Oscar*, *bloquear viernes 15:00* o *ver bloqueos*.
Escribe *salir* para volver al modo cliente 😊"""



def menu_demo():
    return """🧪 *Modo Demo activado*

Este modo es solo para pruebas comerciales desde tu WhatsApp de dueño.
No debe usarse en producción con clientes reales.

1️⃣ *Nuevo cliente demo* — limpia historial y consentimiento de este WhatsApp
2️⃣ *Limpiar historial* — borra solo la memoria conversacional
3️⃣ *Limpiar consentimiento* — vuelve a pedir autorización desde cero
4️⃣ *Cliente autorizado demo* — marca este WhatsApp como autorizado
0️⃣ *Salir demo* — volver al modo cliente

Comandos rápidos:
• *nuevo cliente demo*
• *limpiar historial demo*
• *limpiar consentimiento demo*
• *cliente autorizado demo*
• *salir demo*"""


def es_entrada_modo_demo(mensaje):
    msg = normalizar(mensaje)
    return msg in ["soy demo", "modo demo", "demo", "panel demo"]


def es_salida_modo_demo(mensaje):
    msg = normalizar(mensaje)
    return msg in ["salir demo", "salir modo demo", "cerrar demo", "0"]


def limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=True, autorizar=False):
    """Limpia estado de demo solo para el OWNER_PHONE.
    Permite simular clientes nuevos desde el mismo WhatsApp sin mezclar historial.
    """
    try:
        user_history.pop(remitente, None)
        telefono_limpio = limpiar_telefono_cliente(telefono)
        cliente = buscar_cliente(telefono=telefono_limpio)

        if cliente and limpiar_consentimiento:
            datos = {
                "whatsapp_opt_in": bool(autorizar),
                "opt_in_fecha": datetime.now().isoformat() if autorizar else None,
                "opt_in_texto": "DEMO AUTORIZADO" if autorizar else None,
                "no_contactar": False,
                "no_contactar_fecha": None,
            }
            supabase.table("clientes").update(datos).eq("id", cliente["id"]).execute()
        elif not cliente and autorizar:
            supabase.table("clientes").insert({
                "nombre": "Cliente Demo",
                "telefono": telefono_limpio,
                "whatsapp_opt_in": True,
                "opt_in_fecha": datetime.now().isoformat(),
                "opt_in_texto": "DEMO AUTORIZADO",
                "no_contactar": False,
                "no_contactar_fecha": None,
            }).execute()

        return True
    except Exception as e:
        print("⚠️ No se pudo limpiar estado demo:", e)
        return False


def procesar_comando_demo(mensaje, telefono, remitente):
    msg = normalizar(mensaje)

    if msg in ["1", "nuevo cliente demo", "nuevo demo", "reiniciar demo"]:
        limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=True, autorizar=False)
        demo_sessions[remitente] = False
        owner_sessions[remitente] = False
        return (
            "🧪 Listo. Inicié un *nuevo cliente demo* para este WhatsApp.\n\n"
            "Ahora escribe *hola* y el bot pedirá consentimiento desde cero."
        )

    if msg in ["2", "limpiar historial", "limpiar historial demo", "borrar historial demo"]:
        limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=False, autorizar=False)
        return "🧪 Listo. Limpié solo el historial conversacional de este WhatsApp."

    if msg in ["3", "limpiar consentimiento", "limpiar consentimiento demo", "reset consentimiento"]:
        limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=True, autorizar=False)
        demo_sessions[remitente] = False
        owner_sessions[remitente] = False
        return "🧪 Listo. Limpié consentimiento demo. Ahora escribe *hola* y el bot pedirá autorización desde cero."

    if msg in ["4", "cliente autorizado demo", "autorizar demo", "demo autorizado"]:
        limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=True, autorizar=True)
        demo_sessions[remitente] = False
        owner_sessions[remitente] = False
        return (
            "🧪 Listo. Este WhatsApp quedó como *cliente autorizado demo*.\n"
            "Ahora puedes iniciar una conversación sin que vuelva a pedir consentimiento inicial."
        )

    if es_salida_modo_demo(mensaje):
        demo_sessions[remitente] = False
        return "🧪 Modo Demo cerrado. Volvemos al modo cliente."

    return menu_demo()

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
        "reporte", "resumen", "comandos", "ayuda", "menu",
        "bloquear", "liberar", "bloqueos", "ver bloqueos"
    ]
    return any(palabra in msg for palabra in palabras_clave)
def es_solicitud_baja(mensaje):
    """
    Detecta si el usuario quiere dejar de recibir mensajes automáticos.
    Esto ayuda a cumplir políticas de WhatsApp.
    """
    msg = normalizar(mensaje)

    palabras_baja = [
        "baja",
        "stop",
        "no me escriban",
        "no mas mensajes",
        "no más mensajes",
        "cancelar mensajes",
        "dejar de recibir",
        "no quiero recibir",
        "no contactar"
    ]

    return any(palabra in msg for palabra in palabras_baja)


def es_confirmacion_optin(mensaje):
    """Detecta autorización explícita para recibir mensajes relacionados con la cita."""
    msg = normalizar(mensaje)
    return msg in ["si acepto", "sí acepto", "acepto", "autorizo", "si autorizo", "sí autorizo"]


def es_solicitud_humano(mensaje):
    """Detecta cuando un cliente pide atención de una persona."""
    msg = normalizar(mensaje)
    palabras_humano = [
        "asesor",
        "humano",
        "persona",
        "hablar con alguien",
        "quiero hablar con alguien",
        "llamar",
        "llamada",
        "necesito ayuda",
        "que me contacten",
        "que me contacte",
        "atencion humana",
        "atención humana"
    ]
    return any(p in msg for p in palabras_humano)


def marcar_no_contactar(telefono):
    """
    Marca un cliente como no_contactar en Supabase.
    Funciona aunque el teléfono venga con whatsapp:, con +, sin + o con espacios.
    Si el cliente no existe, lo crea como contacto bloqueado.
    """
    try:
        telefono_original = (telefono or "").replace("whatsapp:", "").strip()
        telefono_limpio = limpiar_telefono_cliente(telefono_original)

        posibles_telefonos = list({
            telefono_original,
            telefono_limpio,
            f"+{telefono_limpio}" if telefono_limpio else "",
        })
        posibles_telefonos = [t for t in posibles_telefonos if t]

        datos_baja = {
            "no_contactar": True,
            "no_contactar_fecha": datetime.now().isoformat()
        }

        cliente_encontrado = None

        for tel in posibles_telefonos:
            res = supabase.table("clientes").select("*").eq("telefono", tel).execute()
            if res.data:
                cliente_encontrado = res.data[0]
                break

        if cliente_encontrado:
            supabase.table("clientes").update(datos_baja).eq("id", cliente_encontrado["id"]).execute()
            print(f"✅ Cliente marcado como no_contactar: {cliente_encontrado.get('telefono')}")
            return True

        nuevo_cliente = {
            "nombre": "Cliente sin nombre",
            "telefono": telefono_limpio,
            "no_contactar": True,
            "no_contactar_fecha": datetime.now().isoformat()
        }

        supabase.table("clientes").insert(nuevo_cliente).execute()
        print(f"✅ Cliente creado y marcado como no_contactar: {telefono_limpio}")
        return True

    except Exception as e:
        print("❌ Error marcando no_contactar:", e)
        return False


def marcar_opt_in(telefono, texto="SI ACEPTO"):
    """Guarda autorización WhatsApp del cliente para mensajes relacionados con la reserva."""
    try:
        telefono_limpio = limpiar_telefono_cliente(telefono)
        cliente = buscar_cliente(telefono=telefono_limpio)

        datos = {
            "whatsapp_opt_in": True,
            "opt_in_fecha": datetime.now().isoformat(),
            "opt_in_texto": texto,
            "no_contactar": False,
            "no_contactar_fecha": None,
        }

        if cliente:
            supabase.table("clientes").update(datos).eq("id", cliente["id"]).execute()
        else:
            datos.update({"nombre": "Cliente WhatsApp", "telefono": telefono_limpio})
            supabase.table("clientes").insert(datos).execute()

        return True

    except Exception as e:
        print("❌ Error marcando opt-in:", e)
        return False


def registrar_solicitud_humano(telefono, nombre=None, motivo=""):
    """Registra una solicitud de atención humana para revisión del dueño/equipo."""
    try:
        supabase.table("solicitudes_humano").insert({
            "telefono": limpiar_telefono_cliente(telefono),
            "nombre": nombre,
            "motivo": motivo,
            "estado": "pendiente",
        }).execute()
        return True
    except Exception as e:
        print("❌ Error registrando solicitud humana:", e)
        return False


def registrar_mensaje_log(telefono, direccion, mensaje, tipo="whatsapp"):
    """Registra mensajes para control de consumo y auditoría básica."""
    try:
        supabase.table("mensajes_log").insert({
            "telefono": limpiar_telefono_cliente(telefono),
            "direccion": direccion,
            "mensaje": mensaje,
            "tipo": tipo,
        }).execute()
    except Exception as e:
        print("⚠️ No se pudo registrar mensaje_log:", e)

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
    """Resumen profesional para el dueño: agenda, ingresos y cambios recientes."""
    try:
        agenda = agenda_del_dia()
        ingresos = ingresos_periodo(0)

        try:
            cambios = historial_citas_texto()
        except Exception as e:
            print(f"ERROR resumen historial: {e}")
            cambios = "No pude consultar los cambios recientes en este momento."

        # Limpiar título repetido del historial para que el resumen se vea mejor
        cambios = cambios.replace("📋 *Últimos cambios de citas*", "").strip()

        if not cambios:
            cambios = "No hay cambios recientes registrados."

        return (
            "📌 *Resumen del día para dueño*\n\n"
            "📅 *Agenda de hoy*\n"
            f"{agenda}\n\n"
            "────────────\n\n"
            "💰 *Ingresos de hoy*\n"
            f"{ingresos}\n\n"
            "────────────\n\n"
            "🔄 *Cambios recientes*\n"
            f"{cambios}"
        )

    except Exception as e:
        print(f"ERROR resumen dueño: {e}")
        return "No pude generar el resumen del dueño en este momento 😊"

def procesar_comando_dueno(mensaje):
    """Procesa comandos del dueño y devuelve respuesta."""
    msg = normalizar(mensaje)

    # Bloqueos de agenda del dueño
    if msg.startswith("bloquear"):
        return bloquear_horario_dueno(mensaje, OWNER_PHONE)

    if msg.startswith("liberar"):
        return liberar_horario_dueno(mensaje)

    if any(p in msg for p in ["ver bloqueos", "bloqueos", "horarios bloqueados"]):
        return ver_bloqueos_dueno()

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

    if msg in ["8", "08", "ocho"]:
        return "🔎 Para buscar escribe: *buscar Nombre*\nEjemplo: *buscar Oscar*"

    if msg in ["9", "09", "nueve"]:
        return historial_citas_texto()

    if msg in ["0", "00", "cero"]:
        return "Escribe *salir* para volver al modo cliente 😊"

    # Buscar cliente/cita
    if msg.startswith("buscar ") or msg.startswith("cliente ") or msg.startswith("cita de "):
        termino = msg.replace("buscar ", "", 1).replace("cliente ", "", 1).replace("cita de ", "", 1).strip()
        return buscar_citas_texto(termino)

    # Historial de cambios
    if msg.startswith("historial "):
        termino = msg.replace("historial ", "", 1).strip()
        return historial_citas_texto(termino)

    if any(p in msg for p in ["historial", "cambios", "reagendadas", "canceladas"]):
        return historial_citas_texto()

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
Eres Valentina, recepción virtual y asesora de bienestar de Spa Bella.
Atiendes por WhatsApp con un tono cálido, cercano y profesional.
Tu función es ayudar a los clientes a conocer servicios, precios, horarios y agendar citas.

ESTILO:
- Conversacional y natural. Como si estuvieras en la recepción del spa.
- Máximo 4 líneas salvo cuando muestres menús numerados de servicios.
- Emojis con moderación: 🌸 💆‍♀️ ✨ 😊 (máximo 2 por mensaje).
- Preséntate como Valentina, la recepción virtual de Spa Bella.
- No expliques detalles técnicos salvo que el cliente pregunte.
- Si el cliente pide hablar con una persona, ofrece escalar con el equipo humano.
- Siempre termina con una invitación a la acción.

REGLAS:
- NUNCA inventes precios. Usa solo los que vienen de la herramienta get_servicios.
- Si preguntan algo fuera del spa (política, noticias, etc.), redirige amablemente.
- NO ofreces: botox, ácido hialurónico, cirugías, láser médico.
- Dirección: Av. Principal 456, Col. Centro, frente al parque 📍
- Horario: Lun-Jue 9am-6pm, Vie 9am-8pm, Sáb 9am-4pm. Domingo cerrado.

BIENVENIDA — PRIMER MENSAJE:
Cuando el cliente saluda por primera vez, preséntate y muestra SIEMPRE el menú de categorías:

"¡Hola! 🌸 Bienvenida a Spa Bella, soy Valentina, la recepción virtual.
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
Cuando una cita queda confirmada por la herramienta guardar_cita, SIEMPRE ofrece agendar otro servicio así:
"¿Te gustaría aprovechar y agendar otro servicio para ese mismo día o en otra fecha? 
Tenemos faciales, depilación, uñas y más 😊"
Si el cliente dice que sí, inicia el flujo de agendamiento desde cero para el nuevo servicio.

REGLA CRÍTICA DE CONFIRMACIÓN:
- NUNCA digas que una cita quedó confirmada, cancelada o reagendada si la herramienta devolvió error.
- Si la herramienta devuelve error, explica suavemente y pide confirmar el dato necesario.
- Usa siempre la fecha exacta devuelta por get_horarios para guardar o reagendar.
- No guardes ni confirmes fechas pasadas.

REAGENDAR Y CANCELAR:
- Si el cliente pide cambiar, mover o reagendar una cita, NO uses guardar_cita.
- Primero identifica la cita del cliente. Si hace falta, usa buscar_citas_cliente.
- Pide o confirma nueva fecha y hora.
- Cuando nueva fecha y hora estén claras, usa reagendar_cita.
- Si el cliente pide cancelar una cita, usa cancelar_cita cuando tengas identificado al cliente o su cita.
- Después de reagendar, aclara que el horario anterior quedó liberado.

CUMPLIMIENTO WHATSAPP:
- El sistema valida el consentimiento antes de mostrar servicios o confirmar citas.
- Si el cliente ya autorizó, NO vuelvas a pedir autorización.
- Si el cliente escribe SI ACEPTO, registra autorización y continúa de forma segura desde el menú; no confirmes citas antiguas por historial viejo.
- Nunca confirmes una cita si el cliente no eligió claramente servicio, fecha, hora y nombre en la conversación actual.

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
            "Úsala cuando el cliente quiere agendar o reagendar y dice un día o fecha. "
            "Devuelve la fecha exacta que luego debes usar para guardar o reagendar."
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
            "fecha y hora confirmados. NO la uses para reagendar; para eso usa reagendar_cita. "
            "La fecha debe ser futura y venir en formato YYYY-MM-DD."
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
                    "description": "Fecha en formato YYYY-MM-DD. Debe ser futura o de hoy."
                },
                "hora": {
                    "type": "string",
                    "description": "Hora en formato HH:MM (ejemplo: 10:00, 14:00)."
                }
            },
            "required": ["nombre_cliente", "telefono", "nombre_servicio", "fecha", "hora"]
        }
    },
    {
        "name": "buscar_citas_cliente",
        "description": (
            "Busca citas de un cliente por nombre o teléfono. "
            "Úsala cuando el cliente quiera cancelar, reagendar o preguntar por su cita."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_cliente": {"type": "string", "description": "Nombre del cliente si lo conoces."},
                "telefono": {"type": "string", "description": "Teléfono del cliente si lo conoces."}
            },
            "required": []
        }
    },
    {
        "name": "reagendar_cita",
        "description": (
            "Reagenda una cita real: cancela la cita anterior, libera ese horario y crea una nueva cita confirmada. "
            "Úsala SOLO cuando el cliente ya confirmó la nueva fecha y hora."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_cliente": {"type": "string", "description": "Nombre del cliente."},
                "telefono": {"type": "string", "description": "Teléfono del cliente."},
                "nombre_servicio": {"type": "string", "description": "Servicio de la cita, si se conoce."},
                "fecha_actual": {"type": "string", "description": "Fecha actual de la cita si se conoce, YYYY-MM-DD."},
                "hora_actual": {"type": "string", "description": "Hora actual de la cita si se conoce, HH:MM."},
                "nueva_fecha": {"type": "string", "description": "Nueva fecha confirmada, YYYY-MM-DD."},
                "nueva_hora": {"type": "string", "description": "Nueva hora confirmada, HH:MM."}
            },
            "required": ["nueva_fecha", "nueva_hora"]
        }
    },
    {
        "name": "cancelar_cita",
        "description": (
            "Cancela una cita real en la base de datos y libera el horario. "
            "Úsala cuando el cliente confirme que desea cancelar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre_cliente": {"type": "string", "description": "Nombre del cliente."},
                "telefono": {"type": "string", "description": "Teléfono del cliente."},
                "nombre_servicio": {"type": "string", "description": "Servicio de la cita si se conoce."},
                "fecha": {"type": "string", "description": "Fecha de la cita si se conoce, YYYY-MM-DD."},
                "hora": {"type": "string", "description": "Hora de la cita si se conoce, HH:MM."}
            },
            "required": []
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



def limpiar_telefono_cliente(numero):
    """Deja solo dígitos para evitar duplicados por whatsapp:+57, +57 o espacios."""
    numero = (numero or "").replace("whatsapp:", "").strip()
    return "".join(c for c in numero if c.isdigit())


def validar_fecha_hora_futura(fecha, hora):
    """Valida fecha/hora antes de guardar, cancelar o reagendar.
    Evita citas en años/fechas pasadas por mala interpretación del modelo.
    """
    try:
        fecha = (fecha or "").strip()
        hora = (hora or "").strip()
        if len(hora) == 4 and hora[1] == ":":
            hora = "0" + hora
        fecha_hora = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
    except Exception:
        return {"error": "La fecha u hora no tienen formato válido. Usa fecha YYYY-MM-DD y hora HH:MM."}

    ahora = datetime.now()
    if fecha_hora < ahora:
        return {
            "error": (
                f"La fecha {fecha} a las {hora} está en el pasado según el sistema. "
                "No guardes la cita. Pide al cliente confirmar nuevamente el día y la hora."
            )
        }

    return {
        "fecha": fecha_hora.strftime("%Y-%m-%d"),
        "hora": fecha_hora.strftime("%H:%M"),
        "fecha_hora": fecha_hora.strftime("%Y-%m-%d %H:%M:00"),
        "dt": fecha_hora,
    }



def extraer_fecha_hora_desde_texto(texto):
    """Extrae fecha y hora desde comandos del dueño como 'bloquear viernes 15:00'."""
    texto_original = texto or ""
    texto_n = normalizar(texto_original)

    hora_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", texto_n)
    if not hora_match:
        # Soporte básico para 3pm, 3 pm, 10am
        hora_match_ampm = re.search(r"\b(1[0-2]|0?[1-9])\s*(am|pm)\b", texto_n)
        if not hora_match_ampm:
            return None, None, None
        h = int(hora_match_ampm.group(1))
        ampm = hora_match_ampm.group(2)
        if ampm == "pm" and h != 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
        hora = f"{h:02d}:00"
    else:
        hora = f"{int(hora_match.group(1)):02d}:{hora_match.group(2)}"

    texto_fecha = texto_n
    for palabra in ["bloquear", "liberar", "horario", "espacio"]:
        texto_fecha = texto_fecha.replace(palabra, " ")
    texto_fecha = re.sub(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", " ", texto_fecha)
    texto_fecha = re.sub(r"\b(1[0-2]|0?[1-9])\s*(am|pm)\b", " ", texto_fecha)
    texto_fecha = " ".join(texto_fecha.split())

    fecha, dia = interpretar_fecha(texto_fecha)
    motivo = texto_original
    return fecha, hora, motivo


def bloquear_horario_dueno(mensaje, telefono):
    """Bloquea un horario para que no aparezca disponible a clientes."""
    fecha, hora, motivo = extraer_fecha_hora_desde_texto(mensaje)
    if not fecha or not hora:
        return "🔒 Para bloquear necesito día y hora. Ejemplo: *bloquear viernes 15:00 reunión personal*."

    valida = validar_fecha_hora_futura(fecha, hora)
    if valida.get("error"):
        return f"🔒 No pude bloquear ese espacio: {valida['error']}"

    try:
        existe = supabase.table("bloqueos_agenda").select("id") \
            .eq("fecha", fecha).eq("hora", hora).eq("activo", True).execute()
        if existe.data:
            return f"🔒 Ese horario ya estaba bloqueado: {fecha} {hora}."

        supabase.table("bloqueos_agenda").insert({
            "fecha": fecha,
            "hora": hora,
            "motivo": motivo,
            "creado_por": limpiar_telefono_cliente(telefono),
            "activo": True,
        }).execute()
        return f"✅ Listo. Bloqueé el {fecha} a las {hora}. Ese espacio ya no aparecerá disponible para clientes."
    except Exception as e:
        print("ERROR bloqueando horario:", e)
        return "No pude bloquear ese horario en este momento."


def liberar_horario_dueno(mensaje):
    """Libera un horario previamente bloqueado."""
    fecha, hora, _ = extraer_fecha_hora_desde_texto(mensaje)
    if not fecha or not hora:
        return "🔓 Para liberar necesito día y hora. Ejemplo: *liberar viernes 15:00*."

    try:
        supabase.table("bloqueos_agenda").update({"activo": False}) \
            .eq("fecha", fecha).eq("hora", hora).eq("activo", True).execute()
        return f"✅ Listo. Liberé el {fecha} a las {hora}. Si no hay cita ocupando ese espacio, volverá a aparecer disponible."
    except Exception as e:
        print("ERROR liberando horario:", e)
        return "No pude liberar ese horario en este momento."


def ver_bloqueos_dueno():
    """Muestra bloqueos activos próximos."""
    try:
        hoy = datetime.now().strftime("%Y-%m-%d")
        res = supabase.table("bloqueos_agenda").select("fecha, hora, motivo, created_at") \
            .gte("fecha", hoy).eq("activo", True).order("fecha").limit(20).execute()
        datos = res.data or []
        if not datos:
            return "📋 No hay bloqueos activos próximos."

        lineas = ["📋 *Bloqueos activos de agenda*\n"]
        for b in datos:
            hora = str(b.get("hora", ""))[:5]
            motivo = b.get("motivo") or "Sin motivo"
            lineas.append(f"🔒 {b.get('fecha')} {hora} — {motivo}")
        return "\n".join(lineas)
    except Exception as e:
        print("ERROR ver bloqueos:", e)
        return "No pude consultar los bloqueos en este momento."

def fecha_legible(fecha_hora):
    """Convierte fecha_hora a texto legible para WhatsApp."""
    try:
        if isinstance(fecha_hora, str):
            dt = datetime.fromisoformat(fecha_hora.replace("Z", "").replace("T", " ")[:19])
        else:
            dt = fecha_hora
        dias = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
        meses = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
        return f"{dias[dt.weekday()]} {dt.day} de {meses[dt.month-1]} a las {dt.strftime('%H:%M')}"
    except Exception:
        return str(fecha_hora)


def registrar_historial(cita_id=None, cliente_id=None, accion="", fecha_anterior=None,
                        fecha_nueva=None, estado_anterior=None, estado_nuevo=None, detalle=""):
    """Guarda trazabilidad de creación, cancelación y reagendamiento."""
    try:
        supabase.table("historial_citas").insert({
            "cita_id": cita_id,
            "cliente_id": cliente_id,
            "accion": accion,
            "fecha_anterior": fecha_anterior,
            "fecha_nueva": fecha_nueva,
            "estado_anterior": estado_anterior,
            "estado_nuevo": estado_nuevo,
            "detalle": detalle,
        }).execute()
    except Exception as e:
        # No debe romper el flujo principal si falla el historial.
        print(f"ERROR historial_citas: {e}")


def buscar_cliente(nombre_cliente=None, telefono=None):
    """Busca un cliente por teléfono o por parte del nombre."""
    telefono_limpio = limpiar_telefono_cliente(telefono)
    try:
        if telefono_limpio:
            res = supabase.table("clientes").select("*").eq("telefono", telefono_limpio).execute()
            if res.data:
                return res.data[0]
            # Compatibilidad con números guardados con +
            res = supabase.table("clientes").select("*").eq("telefono", "+" + telefono_limpio).execute()
            if res.data:
                return res.data[0]
        if nombre_cliente:
            res = supabase.table("clientes").select("*").ilike("nombre", f"%{nombre_cliente}%").execute()
            if res.data:
                return res.data[0]
    except Exception as e:
        print(f"ERROR buscar_cliente: {e}")
    return None


def citas_activas_cliente(cliente_id, desde_ahora=True):
    """Devuelve citas no canceladas del cliente, ordenadas por fecha."""
    try:
        q = supabase.table("citas") \
            .select("id, cliente_id, servicio_id, fecha_hora, estado, clientes(nombre, telefono), servicios(nombre, precio)") \
            .eq("cliente_id", cliente_id) \
            .neq("estado", "cancelada")
        if desde_ahora:
            q = q.gte("fecha_hora", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        res = q.order("fecha_hora").execute()
        return res.data or []
    except Exception as e:
        print(f"ERROR citas_activas_cliente: {e}")
        return []


def elegir_cita_para_operacion(cliente, nombre_servicio=None, fecha=None, hora=None):
    """Elige la cita activa más probable para cancelar o reagendar."""
    citas = citas_activas_cliente(cliente["id"], desde_ahora=True)
    if not citas:
        return None, []

    filtradas = citas
    if nombre_servicio:
        ns = normalizar(nombre_servicio)
        filtradas = [c for c in filtradas if ns in normalizar((c.get("servicios") or {}).get("nombre", ""))]

    if fecha:
        filtradas = [c for c in filtradas if str(c.get("fecha_hora", ""))[:10] == fecha]

    if hora:
        hora_norm = hora.strip()
        if len(hora_norm) == 4 and hora_norm[1] == ":":
            hora_norm = "0" + hora_norm
        filtradas = [c for c in filtradas if str(c.get("fecha_hora", ""))[11:16] == hora_norm]

    if not filtradas:
        return None, citas
    return filtradas[0], citas


def formatear_cita(c):
    cliente = c.get("clientes") or {}
    servicio = c.get("servicios") or {}
    precio = servicio.get("precio", 0)
    return (
        f"📌 {fecha_legible(c.get('fecha_hora'))}\n"
        f"Cliente: {cliente.get('nombre', 'Sin nombre')}\n"
        f"Tel: {cliente.get('telefono', 'Sin teléfono')}\n"
        f"Servicio: {servicio.get('nombre', 'Sin servicio')}\n"
        f"Precio: {formatear_precio(precio)}\n"
        f"Estado: {c.get('estado', 'sin estado')}"
    )


def buscar_citas_texto(termino):
    """Reporte para el dueño: busca cliente y muestra sus citas activas."""
    termino = (termino or "").strip()
    if not termino:
        return "🔎 Escribe a quién quieres buscar. Ejemplo: *buscar Oscar*"

    cliente = buscar_cliente(nombre_cliente=termino, telefono=termino)
    if not cliente:
        return f"🔎 No encontré clientes con: {termino}"

    citas = citas_activas_cliente(cliente["id"], desde_ahora=False)
    if not citas:
        return (
            f"🔎 Cliente encontrado:\n"
            f"{cliente.get('nombre')}\nTel: {cliente.get('telefono')}\n\n"
            "No tiene citas registradas."
        )

    lineas = [
        f"🔎 *Cliente encontrado*\n{cliente.get('nombre')}\nTel: {cliente.get('telefono')}\n",
        f"Citas registradas: {len(citas)}\n"
    ]
    for c in citas[-8:]:
        servicio = c.get("servicios") or {}
        estado = "✅" if c.get("estado") == "confirmada" else "⏳"
        lineas.append(f"{estado} {fecha_legible(c.get('fecha_hora'))} — {servicio.get('nombre', 'Sin servicio')}")
    return "\n".join(lineas)


def historial_citas_texto(termino=None):
    """Reporte para el dueño: historial general o filtrado por cliente."""
    try:
        cliente = None

        # 1) Buscar cliente si el dueño escribió: historial Oscar / historial 300...
        if termino:
            cliente = buscar_cliente(nombre_cliente=termino, telefono=termino)
            if not cliente:
                return f"📋 No encontré cliente para consultar historial: {termino}"

        # 2) Consultar historial SIN join embebido para evitar error PGRST200
        q = supabase.table("historial_citas").select("*")

        if cliente:
            q = q.eq("cliente_id", cliente["id"])

        res = q.order("creado_en", desc=True).limit(10).execute()
        datos = res.data or []

        if not datos:
            if termino:
                return f"📋 No encontré historial para {termino}."
            return "📋 No hay historial de cambios todavía."

        # 3) Traer clientes aparte
        cliente_ids = list({h.get("cliente_id") for h in datos if h.get("cliente_id")})
        clientes_map = {}

        if cliente_ids:
            clientes_res = (
                supabase.table("clientes")
                .select("id, nombre, telefono")
                .in_("id", cliente_ids)
                .execute()
            )
            for c in clientes_res.data or []:
                clientes_map[c["id"]] = c

        titulo = (
            f"📋 *Historial de {cliente.get('nombre')}*"
            if cliente
            else "📋 *Últimos cambios de citas*"
        )

        lineas = [titulo, ""]

        for h in datos:
            cli = clientes_map.get(h.get("cliente_id"), {})
            nombre_cliente = cli.get("nombre", "Cliente")
            telefono_cliente = cli.get("telefono", "")

            accion = h.get("accion", "cambio")
            anterior = h.get("fecha_anterior") or "-"
            nueva = h.get("fecha_nueva") or "-"
            estado_anterior = h.get("estado_anterior") or "-"
            estado_nuevo = h.get("estado_nuevo") or "-"
            detalle = h.get("detalle") or ""
            creado_en = h.get("creado_en") or ""

            lineas.append(f"🔹 *{accion}*")
            lineas.append(f"Cliente: {nombre_cliente}")
            if telefono_cliente:
                lineas.append(f"Tel: {telefono_cliente}")
            lineas.append(f"Antes: {anterior}")
            lineas.append(f"Ahora: {nueva}")
            lineas.append(f"Estado: {estado_anterior} → {estado_nuevo}")
            if detalle:
                lineas.append(f"Detalle: {detalle}")
            if creado_en:
                lineas.append(f"Registro: {creado_en}")
            lineas.append("")

        return "\n".join(lineas).strip()

    except Exception as e:
        print(f"ERROR historial dueño: {e}")
        return "No pude consultar el historial en este momento 😊"
    
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

        # Seguridad: no ofrecer horarios en fechas pasadas
        try:
            fecha_consulta = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            hoy_fecha = datetime.now().date()

            if fecha_consulta < hoy_fecha:
                return {
                    "error": (
                        f"Esa fecha ({fecha_str}) ya pasó. "
                        "Pide al cliente una fecha futura para poder agendar."
                    )
                }
        except Exception as e:
            print(f"ERROR validando fecha en get_horarios: {e}")
            return {"error": "No pude validar la fecha. Pide al cliente que la escriba nuevamente."}

        if dia_semana == "domingo":
            return {"error": "Los domingos estamos cerrados. Pide otro día."}
        if dia_semana not in HORARIOS_DISPONIBLES:
            return {"error": f"No tenemos horarios para '{dia_semana}'. Pide un día válido."}

        horarios_base = HORARIOS_DISPONIBLES[dia_semana]
        inicio = f"{fecha_str} 00:00:00"
        fin    = f"{fecha_str} 23:59:59"

        try:
            # 1) Horarios ocupados por citas existentes
            citas = supabase.table("citas").select("fecha_hora") \
                .gte("fecha_hora", inicio).lte("fecha_hora", fin) \
                .neq("estado", "cancelada").execute()
            ocupadas = {str(c.get("fecha_hora", ""))[11:16] for c in (citas.data or [])}

            # 2) Horarios bloqueados por el dueño desde el panel privado
            bloqueos = supabase.table("bloqueos_agenda").select("hora") \
                .eq("fecha", fecha_str) \
                .eq("activo", True) \
                .execute()
            bloqueadas = {str(b.get("hora", ""))[:5] for b in (bloqueos.data or [])}

            # 3) Disponibles reales = horarios base menos citas ocupadas y bloqueos activos
            no_disponibles = ocupadas | bloqueadas
            disponibles = [h for h in horarios_base if h not in no_disponibles]

            print(f"🕒 Horarios {fecha_str}: ocupadas={ocupadas}, bloqueadas={bloqueadas}, disponibles={disponibles}")

        except Exception as e:
            print(f"ERROR horarios: {e}")
            disponibles = horarios_base

        if not disponibles:
            return {
                "fecha": fecha_str,
                "dia": dia_semana,
                "error": f"Para el {dia_semana} ya no hay cupos disponibles."
            }

        return {
            "fecha": fecha_str,
            "dia": dia_semana,
            "disponibles": disponibles
        }

    # ── guardar_cita ───────────────────────────────────────────
    elif tool_name == "guardar_cita":
        nombre   = tool_input["nombre_cliente"]
        # Cumplimiento WhatsApp: la autorización pertenece al número real que escribe por WhatsApp.
        # No usamos el teléfono que el cliente escriba en el chat para validar opt-in.
        telefono = limpiar_telefono_cliente(telefono_remitente)
        nombre_s = tool_input["nombre_servicio"]
        fecha    = tool_input["fecha"]
        hora     = tool_input["hora"]

        valida_fecha = validar_fecha_hora_futura(fecha, hora)
        if valida_fecha.get("error"):
            return {"error": valida_fecha["error"]}
        fecha = valida_fecha["fecha"]
        hora = valida_fecha["hora"]
        fecha_hora_completa = valida_fecha["fecha_hora"]

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

        # Cumplimiento WhatsApp: no confirmar citas sin autorización explícita
        if cliente.get("no_contactar"):
            return {
                "error": (
                    "Este cliente solicitó no recibir mensajes automáticos. "
                    "Pide atención humana antes de continuar."
                )
            }

        if not cliente.get("whatsapp_opt_in"):
            return {
                "error": (
                    "Antes de confirmar la cita, pide autorización explícita. "
                    "Envía este texto al cliente: Para confirmar tu cita y poder enviarte mensajes relacionados con esta reserva por WhatsApp, "
                    "¿autorizas a Spa Bella a contactarte por este medio? Responde: SI ACEPTO. "
                    "No confirmes la cita hasta que responda SI ACEPTO."
                )
            }

        # Verificar que el horario sigue libre
        try:
            ocupado = supabase.table("citas").select("id") \
                .eq("fecha_hora", fecha_hora_completa) \
                .neq("estado", "cancelada").execute()
            if ocupado.data:
                return {"error": f"El horario {hora} del {fecha} acaba de ocuparse. Pide al cliente que elija otro."}

            bloqueado = supabase.table("bloqueos_agenda").select("id")                 .eq("fecha", fecha).eq("hora", hora).eq("activo", True).execute()
            if bloqueado.data:
                return {"error": f"El horario {hora} del {fecha} no está disponible. Pide al cliente que elija otro."}
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
                cita_creada = cita.data[0]
                registrar_historial(
                    cita_id=cita_creada.get("id"),
                    cliente_id=cliente["id"],
                    accion="cita_creada",
                    fecha_nueva=fecha_hora_completa,
                    estado_nuevo="confirmada",
                    detalle=f"Cita creada para {nombre} — {nombre_s}"
                )
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


    # ── buscar_citas_cliente ──────────────────────────────────
    elif tool_name == "buscar_citas_cliente":
        nombre = tool_input.get("nombre_cliente")
        telefono = limpiar_telefono_cliente(tool_input.get("telefono") or telefono_remitente)
        cliente = buscar_cliente(nombre_cliente=nombre, telefono=telefono)
        if not cliente:
            return {"error": "No encontré al cliente. Pide nombre completo o teléfono."}

        citas = citas_activas_cliente(cliente["id"], desde_ahora=True)
        if not citas:
            return {
                "cliente": cliente,
                "citas": [],
                "mensaje": "El cliente existe, pero no tiene citas activas próximas."
            }

        return {
            "cliente": cliente,
            "citas": [
                {
                    "id": c.get("id"),
                    "fecha_hora": c.get("fecha_hora"),
                    "estado": c.get("estado"),
                    "servicio": (c.get("servicios") or {}).get("nombre"),
                    "precio": (c.get("servicios") or {}).get("precio"),
                    "texto": formatear_cita(c),
                }
                for c in citas
            ]
        }

    # ── cancelar_cita ──────────────────────────────────────────
    elif tool_name == "cancelar_cita":
        nombre = tool_input.get("nombre_cliente")
        telefono = limpiar_telefono_cliente(tool_input.get("telefono") or telefono_remitente)
        nombre_s = tool_input.get("nombre_servicio")
        fecha = tool_input.get("fecha")
        hora = tool_input.get("hora")

        cliente = buscar_cliente(nombre_cliente=nombre, telefono=telefono)
        if not cliente:
            return {"error": "No encontré al cliente. Pide nombre completo o teléfono para cancelar."}

        cita_actual, citas_cliente = elegir_cita_para_operacion(cliente, nombre_s, fecha, hora)
        if not cita_actual:
            return {
                "error": "No encontré una cita activa que coincida para cancelar.",
                "citas_activas": [formatear_cita(c) for c in citas_cliente[:5]]
            }

        fecha_anterior = cita_actual.get("fecha_hora")
        try:
            supabase.table("citas").update({"estado": "cancelada"}).eq("id", cita_actual["id"]).execute()
            registrar_historial(
                cita_id=cita_actual["id"],
                cliente_id=cliente["id"],
                accion="cita_cancelada",
                fecha_anterior=fecha_anterior,
                estado_anterior=cita_actual.get("estado"),
                estado_nuevo="cancelada",
                detalle=f"Cita cancelada para {cliente.get('nombre')}"
            )
            return {
                "exito": True,
                "mensaje": "Cita cancelada correctamente. El horario quedó liberado.",
                "cliente": cliente.get("nombre"),
                "fecha_anterior": fecha_anterior,
                "cita_cancelada": formatear_cita(cita_actual),
            }
        except Exception as e:
            print(f"ERROR cancelar cita: {e}")
            return {"error": "No pude cancelar la cita en este momento."}

    # ── reagendar_cita ─────────────────────────────────────────
    elif tool_name == "reagendar_cita":
        nombre = tool_input.get("nombre_cliente")
        telefono = limpiar_telefono_cliente(tool_input.get("telefono") or telefono_remitente)
        nombre_s = tool_input.get("nombre_servicio")
        fecha_actual = tool_input.get("fecha_actual")
        hora_actual = tool_input.get("hora_actual")
        nueva_fecha = tool_input.get("nueva_fecha")
        nueva_hora = tool_input.get("nueva_hora")

        valida_fecha = validar_fecha_hora_futura(nueva_fecha, nueva_hora)
        if valida_fecha.get("error"):
            return {"error": valida_fecha["error"]}
        nueva_fecha_hora = valida_fecha["fecha_hora"]
        nueva_fecha = valida_fecha["fecha"]
        nueva_hora = valida_fecha["hora"]

        cliente = buscar_cliente(nombre_cliente=nombre, telefono=telefono)
        if not cliente:
            return {"error": "No encontré al cliente. Pide nombre completo o teléfono para reagendar."}

        cita_actual, citas_cliente = elegir_cita_para_operacion(cliente, nombre_s, fecha_actual, hora_actual)
        if not cita_actual:
            return {
                "error": "No encontré una cita activa que coincida para reagendar.",
                "citas_activas": [formatear_cita(c) for c in citas_cliente[:5]]
            }

        fecha_anterior = cita_actual.get("fecha_hora")

        try:
            ocupado = supabase.table("citas").select("id") \
                .eq("fecha_hora", nueva_fecha_hora) \
                .neq("estado", "cancelada") \
                .neq("id", cita_actual["id"]) \
                .execute()
            if ocupado.data:
                return {"error": f"El horario {nueva_hora} del {nueva_fecha} ya está ocupado. Pide otro horario."}

            bloqueado = supabase.table("bloqueos_agenda").select("id")                 .eq("fecha", nueva_fecha).eq("hora", nueva_hora).eq("activo", True).execute()
            if bloqueado.data:
                return {"error": f"El horario {nueva_hora} del {nueva_fecha} está bloqueado por el negocio. Pide otro horario."}
        except Exception as e:
            print(f"ERROR verificando horario reagenda: {e}")

        try:
            # Cancelamos la anterior para liberar el horario y conservar trazabilidad.
            supabase.table("citas").update({"estado": "cancelada"}).eq("id", cita_actual["id"]).execute()

            nueva = supabase.table("citas").insert({
                "cliente_id": cita_actual["cliente_id"],
                "servicio_id": cita_actual["servicio_id"],
                "fecha_hora": nueva_fecha_hora,
                "estado": "confirmada",
            }).execute()

            if not nueva.data:
                # Si falla crear la nueva, intentamos devolver la anterior a confirmada.
                try:
                    supabase.table("citas").update({"estado": cita_actual.get("estado", "confirmada")}).eq("id", cita_actual["id"]).execute()
                except Exception:
                    pass
                return {"error": "No pude crear la nueva cita. La cita anterior se conservó."}

            nueva_cita = nueva.data[0]
            registrar_historial(
                cita_id=cita_actual["id"],
                cliente_id=cliente["id"],
                accion="cita_reagendada",
                fecha_anterior=fecha_anterior,
                fecha_nueva=nueva_fecha_hora,
                estado_anterior=cita_actual.get("estado"),
                estado_nuevo="confirmada",
                detalle=f"Reagendada para {cliente.get('nombre')}. Horario anterior liberado. Nueva cita: {nueva_cita.get('id')}"
            )
            servicio = cita_actual.get("servicios") or {}
            return {
                "exito": True,
                "mensaje": "Cita reagendada correctamente. El horario anterior quedó liberado.",
                "cliente": cliente.get("nombre"),
                "servicio": servicio.get("nombre"),
                "fecha_anterior": fecha_anterior,
                "fecha_nueva": nueva_fecha_hora,
                "cita_anterior_cancelada_id": cita_actual["id"],
                "nueva_cita_id": nueva_cita.get("id"),
            }
        except Exception as e:
            print(f"ERROR reagendar cita: {e}")
            return {"error": "No pude reagendar la cita en este momento."}

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

        fecha_actual = datetime.now().strftime("%Y-%m-%d")
        dia_actual = DIAS_ES.get(datetime.now().strftime("%A").lower(), datetime.now().strftime("%A"))
        system_runtime = (
            SYSTEM_PROMPT
            + f"\n\nFECHA ACTUAL DEL SISTEMA: {fecha_actual} ({dia_actual}). "
            + "Usa esta fecha como referencia para interpretar hoy, mañana, esta semana y próximas fechas. "
            + "Nunca uses años pasados para agendar, cancelar o reagendar."
        )

        respuesta = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=900,
            system=system_runtime,
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


def menu_servicios_post_optin(reactivacion=False):
    encabezado = (
        "Perfecto, gracias 😊 Ya registré tu autorización nuevamente."
        if reactivacion else
        "Perfecto, gracias 😊 Ya registré tu autorización."
    )
    return (
        f"{encabezado}\n\n"
        "Ahora sí, cuéntame qué te gustaría explorar hoy:\n\n"
        "1️⃣ Masajes — relajante, descontracturante\n"
        "2️⃣ Faciales — hidratación, anti-edad, limpieza\n"
        "3️⃣ Depilación — piernas, axilas\n"
        "4️⃣ Uñas — manicure clásico y semipermanente\n"
        "5️⃣ Pestañas — diseño y lifting\n"
        "6️⃣ Cabello — keratina, tinte y corte\n"
        "7️⃣ Tratamientos corporales — reductivos\n\n"
        "Responde con el número o cuéntame qué buscas 😊"
    )


def responder_post_optin(telefono, remitente, mensaje, reactivacion=False):
    """Registra autorización WhatsApp de forma segura.

    V12.5:
    - Registra opt-in.
    - Limpia historial conversacional viejo para evitar confirmar citas antiguas.
    - No retoma automáticamente conversaciones pendientes.
    - Muestra menú normal para que el cliente continúe conscientemente.
    """
    marcar_opt_in(telefono, mensaje)

    try:
        user_history.pop(remitente, None)
    except Exception:
        pass

    return menu_servicios_post_optin(reactivacion=reactivacion)

# ═══════════════════════════════════════════════════════════════
# ENDPOINT PRINCIPAL
# ═══════════════════════════════════════════════════════════════
@app.route("/health", methods=["GET"])
def health():
    return {
        "ok": True,
        "service": "VEL-AI Agenda Spa Bella",
        "version": "V12.5",
        "time": datetime.now().isoformat(),
        "demo_mode": DEMO_MODE,
    }, 200


@app.route("/bot", methods=["POST"])
def bot():
    mensaje   = request.form.get("Body", "").strip()
    remitente = request.form.get("From", "")
    telefono  = remitente.replace("whatsapp:", "")

    print(f"\n📩 {remitente}: {mensaje}")

    # Log de mensaje entrante para auditoría y control de consumo.
    # No rompe el flujo si Supabase falla, porque registrar_mensaje_log ya maneja excepciones.
    registrar_mensaje_log(telefono, "entrante", mensaje, "whatsapp")

    # ── ATAJOS DEMO DIRECTOS SOLO PARA DUEÑO ───────────────────
    # Permite limpiar demo sin tener que entrar primero con "soy demo".
    if DEMO_MODE and es_dueno(telefono):
        msg_demo_directo = normalizar(mensaje)

        if msg_demo_directo in ["nuevo cliente demo", "nuevo demo", "reiniciar demo"]:
            limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=True, autorizar=False)
            demo_sessions[remitente] = False
            owner_sessions[remitente] = False
            respuesta_texto = """🧪 Listo. Inicié un *nuevo cliente demo* para este WhatsApp.

Ahora escribe *hola* y el bot pedirá consentimiento desde cero."""
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "demo_directo")
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            return Response(str(resp), mimetype="application/xml")

        if msg_demo_directo in ["cliente autorizado demo", "autorizar demo", "demo autorizado"]:
            limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=True, autorizar=True)
            demo_sessions[remitente] = False
            owner_sessions[remitente] = False
            respuesta_texto = """🧪 Listo. Dejé este WhatsApp como *cliente autorizado demo*.

Ahora escribe *hola* y el bot mostrará el menú sin pedir consentimiento."""
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "demo_directo")
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            return Response(str(resp), mimetype="application/xml")

        if msg_demo_directo in ["limpiar consentimiento demo", "reset consentimiento demo"]:
            limpiar_estado_demo(telefono, remitente, limpiar_consentimiento=True, autorizar=False)
            demo_sessions[remitente] = False
            owner_sessions[remitente] = False
            respuesta_texto = """🧪 Listo. Limpié consentimiento demo.

Ahora escribe *hola* y el bot pedirá autorización desde cero."""
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "demo_directo")
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            return Response(str(resp), mimetype="application/xml")

    # ── MODO DEMO SOLO PARA EL DUEÑO ───────────────────────────
    if DEMO_MODE and es_dueno(telefono):
        if es_entrada_modo_demo(mensaje):
            demo_sessions[remitente] = True
            respuesta_texto = menu_demo()
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "demo")
            return Response(str(resp), mimetype="application/xml")

        if demo_sessions.get(remitente):
            respuesta_texto = procesar_comando_demo(mensaje, telefono, remitente)
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "demo")
            return Response(str(resp), mimetype="application/xml")

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

    # ── CLIENTE SIN AUTORIZACIÓN / NO CONTACTAR ───────────────
    telefono_limpio_check = limpiar_telefono_cliente(telefono)
    cliente_check = buscar_cliente(telefono=telefono_limpio_check)

    if cliente_check and cliente_check.get("no_contactar"):
        msg_normalizado = normalizar(mensaje)

        if msg_normalizado in ["1", "si", "si acepto", "sí acepto", "acepto", "autorizo", "si autorizo", "sí autorizo"]:
            respuesta_texto = responder_post_optin(telefono, remitente, mensaje, reactivacion=True)
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "reactivacion_opt_in")
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            return Response(str(resp), mimetype="application/xml")

        respuesta_texto = (
            "Actualmente no tenemos tu autorización para continuar la atención automática por WhatsApp. "
            "Si deseas recibir ayuda para agendar o consultar servicios, responde *SI ACEPTO* 😊"
        )
        registrar_mensaje_log(telefono, "saliente", respuesta_texto, "no_contactar_bloqueado")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
        return Response(str(resp), mimetype="application/xml")

    # ── CONSENTIMIENTO INICIAL ANTES DE MOSTRAR SERVICIOS ─────
    telefono_limpio = limpiar_telefono_cliente(telefono)
    cliente_actual = buscar_cliente(telefono=telefono_limpio)

    if not cliente_actual or (
        not cliente_actual.get("whatsapp_opt_in") and not cliente_actual.get("no_contactar")
    ):
        msg_normalizado = normalizar(mensaje)

        if msg_normalizado in ["1", "si", "si acepto", "sí acepto", "acepto", "autorizo", "si autorizo", "sí autorizo"]:
            respuesta_texto = responder_post_optin(telefono, remitente, mensaje, reactivacion=False)
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "opt_in_inicial")
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            return Response(str(resp), mimetype="application/xml")

        if msg_normalizado in ["2", "no", "no acepto", "rechazo", "no autorizo"]:
            marcar_no_contactar(telefono)
            respuesta_texto = (
                "Entiendo. No registraremos tu autorización para continuar por WhatsApp. "
                "Si más adelante deseas recibir ayuda para agendar, puedes escribir *SI ACEPTO* 😊"
            )
            registrar_mensaje_log(telefono, "saliente", respuesta_texto, "no_acepta")
            resp = MessagingResponse()
            resp.message(respuesta_texto)
            return Response(str(resp), mimetype="application/xml")

        respuesta_texto = (
            "¡Hola! 🌸 Bienvenido/a a Spa Bella, soy Valentina, la recepción virtual.\n"
            "Estoy aquí para consentirte.\n\n"
            "Antes de continuar, ¿aceptas que Spa Bella trate tus datos para atender tu solicitud, "
            "gestionar tu cita y enviarte mensajes relacionados con tu reserva por WhatsApp?\n\n"
            "1️⃣ Sí acepto\n"
            "2️⃣ No acepto"
        )
        registrar_mensaje_log(telefono, "saliente", respuesta_texto, "consentimiento_inicial")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
        return Response(str(resp), mimetype="application/xml")
    
    # ── CUMPLIMIENTO WHATSAPP: BAJA / OPT-IN / HUMANO ─────────
    if es_solicitud_baja(mensaje):
        marcar_no_contactar(telefono)
        respuesta_texto = (
            "Listo, registramos tu solicitud. No volveremos a enviarte mensajes automáticos por WhatsApp, "
            "salvo que tú nos escribas nuevamente."
        )
        registrar_mensaje_log(telefono, "saliente", respuesta_texto, "baja")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
        return Response(str(resp), mimetype="application/xml")

    if es_confirmacion_optin(mensaje):
        respuesta_texto = responder_post_optin(telefono, remitente, mensaje, reactivacion=False)
        registrar_mensaje_log(telefono, "saliente", respuesta_texto, "opt_in")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
        return Response(str(resp), mimetype="application/xml")

    if es_solicitud_humano(mensaje):
        registrar_solicitud_humano(telefono, motivo=mensaje)
        respuesta_texto = (
            "Claro 😊 Dejé registrada tu solicitud para que el equipo de Spa Bella te contacte. "
            "Por favor envíanos tu nombre y el motivo de la consulta para ayudarte mejor."
        )
        registrar_mensaje_log(telefono, "saliente", respuesta_texto, "humano")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
        return Response(str(resp), mimetype="application/xml")

    # ── RESPUESTAS RÁPIDAS SIN IA PARA AHORRAR COSTOS ─────────
    msg_rapido = normalizar(mensaje)

    if msg_rapido in ["menu", "servicios", "ver servicios", "inicio"]:
        respuesta_texto = """Claro 😊 Estos son nuestros servicios principales:

1️⃣ Masajes — relajante, descontracturante
2️⃣ Faciales — hidratación, anti-edad, limpieza
3️⃣ Depilación — piernas, axilas
4️⃣ Uñas — manicure clásico y semipermanente
5️⃣ Pestañas — diseño y lifting
6️⃣ Cabello — keratina, tinte y corte
7️⃣ Tratamientos corporales — reductivos

Responde con el número o cuéntame qué buscas 😊"""
        registrar_mensaje_log(telefono, "saliente", respuesta_texto, "menu_sin_ia")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
        return Response(str(resp), mimetype="application/xml")

    if msg_rapido in ["ayuda", "opciones"]:
        respuesta_texto = """Puedo ayudarte con:

1️⃣ Ver servicios
2️⃣ Consultar horarios
3️⃣ Agendar una cita
4️⃣ Cancelar o reagendar
5️⃣ Hablar con una persona

Responde con el número o dime qué necesitas 😊"""
        registrar_mensaje_log(telefono, "saliente", respuesta_texto, "ayuda_sin_ia")
        resp = MessagingResponse()
        resp.message(respuesta_texto)
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
    registrar_mensaje_log(telefono, "saliente", respuesta_texto, "whatsapp")

    resp = MessagingResponse()
    resp.message(respuesta_texto)
    return Response(str(resp), mimetype="application/xml")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
