import os
import re
import json
import math
import time
import asyncio
import requests
import urllib3
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========== CONFIGURACIÓN ==========
TOKEN = os.environ.get("BOT_TOKEN", "8961602834:AAGj_s3E6t2ZOt6lYdPmVAaUFIQWEq9tVCI")
ADMIN_ID = 8625261339
CANAL = "@BancaYDivisaVe"

COMISION_INTERVENCION = 0.005
COMISION_MENUDEO = 0.012
CACHE_USDT_SEGUNDOS = 45
INTERVALO_BUSQUEDA = 30
ARCHIVO_TASAS = "tasas.json"
ARCHIVO_PLANTILLA = "plantilla.jpg"
ARCHIVO_FUENTE = "DejaVuSans-Bold.ttf"
# ==================================

tasas_actuales = {
    "usd": None,
    "eur": None,
    "fecha": None,
    "usd_anterior": None
}
cache_usdt = {"compra": None, "venta": None, "timestamp": 0}

def es_admin(user_id):
    return user_id == ADMIN_ID

def redondear(numero):
    return float(Decimal(str(numero)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def formatear_numero(numero):
    numero = redondear(numero)
    return f"{numero:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fecha_espanol(fecha_str=None):
    dias = {
        "Monday": "LUNES", "Tuesday": "MARTES", "Wednesday": "MIÉRCOLES",
        "Thursday": "JUEVES", "Friday": "VIERNES", "Saturday": "SÁBADO", "Sunday": "DOMINGO"
    }
    meses = {
        "January": "Enero", "February": "Febrero", "March": "Marzo", "April": "Abril",
        "May": "Mayo", "June": "Junio", "July": "Julio", "August": "Agosto",
        "September": "Septiembre", "October": "Octubre", "November": "Noviembre", "December": "Diciembre"
    }
    if fecha_str:
        anio, mes, dia = fecha_str.split("-")
        fecha = datetime(int(anio), int(mes), int(dia))
    else:
        fecha = datetime.now()
    return f"{dias[fecha.strftime('%A')]}, {int(fecha.day)} de {meses[fecha.strftime('%B')]} {fecha.year}"

def fecha_imagen(fecha_str=None):
    dias = {
        "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
        "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo"
    }
    meses = {
        "January": "Enero", "February": "Febrero", "March": "Marzo", "April": "Abril",
        "May": "Mayo", "June": "Junio", "July": "Julio", "August": "Agosto",
        "September": "Septiembre", "October": "Octubre", "November": "Noviembre", "December": "Diciembre"
    }
    if fecha_str:
        anio, mes, dia = fecha_str.split("-")
        fecha = datetime(int(anio), int(mes), int(dia))
    else:
        fecha = datetime.now()
    return f"{dias[fecha.strftime('%A')]}, {int(fecha.day)} De {meses[fecha.strftime('%B')]} {fecha.year}"

def guardar_tasas():
    try:
        with open(ARCHIVO_TASAS, "w", encoding="utf-8") as f:
            json.dump(tasas_actuales, f)
    except Exception as e:
        print(f"Error guardando tasas: {e}")

def cargar_tasas():
    global tasas_actuales
    try:
        if os.path.exists(ARCHIVO_TASAS):
            with open(ARCHIVO_TASAS, "r", encoding="utf-8") as f:
                data = json.load(f)
                tasas_actuales.update(data)
    except Exception as e:
        print(f"Error cargando tasas: {e}")

def consultar_tasas():
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-VE,es;q=0.9",
        }
        r = requests.get(
            "https://www.bcv.org.ve/",
            headers=headers,
            timeout=20,
            verify=False,
        )
        r.raise_for_status()
        html = r.text

        m_usd = re.search(
            r'id="dolar"[^>]*>.*?class="strong-tb">\s*([\d.,]+)',
            html,
            re.S | re.I,
        )
        m_eur = re.search(
            r'id="euro"[^>]*>.*?class="strong-tb">\s*([\d.,]+)',
            html,
            re.S | re.I,
        )
        m_fecha = re.search(
            r'Fecha Valor:.*?content="(\d{4}-\d{2}-\d{2})',
            html,
            re.S | re.I,
        )

        if not m_usd or not m_eur:
            raise ValueError("No se encontraron tasas en la página del BCV")

        def to_float(texto):
            return float(texto.strip().replace(",", "."))

        usd = to_float(m_usd.group(1))
        eur = to_float(m_eur.group(1))
        fecha = m_fecha.group(1) if m_fecha else datetime.now().strftime("%Y-%m-%d")

        return {"usd": usd, "eur": eur, "fecha": fecha}

    except Exception as e:
        print(f"Error consultando BCV oficial: {e}")
        try:
            r = requests.get("https://rates.dolarvzla.com/bcv/current.json", timeout=10)
            data = r.json()
            return {
                "usd": float(data["current"]["usd"]),
                "eur": float(data["current"]["eur"]),
                "fecha": data["current"]["date"],
            }
        except Exception as e2:
            print(f"Error en fuente de respaldo: {e2}")
            return None

def actualizar_tasas():
    global tasas_actuales
    nuevas = consultar_tasas()
    if not nuevas:
        return False
    aplicar_nuevas_tasas(nuevas)
    print(f"Tasas actualizadas → USD: {tasas_actuales['usd']} | EUR: {tasas_actuales['eur']}")
    return True

def aplicar_nuevas_tasas(nuevas):
    global tasas_actuales
    if tasas_actuales["usd"] is not None and tasas_cambiaron(nuevas):
        tasas_actuales["usd_anterior"] = tasas_actuales["usd"]
    tasas_actuales["usd"] = nuevas["usd"]
    tasas_actuales["eur"] = nuevas["eur"]
    tasas_actuales["fecha"] = nuevas["fecha"]
    guardar_tasas()

def tasas_cambiaron(nuevas):
    if tasas_actuales["usd"] is None:
        return True
    return (
        abs(nuevas["usd"] - tasas_actuales["usd"]) > 0.0001
        or abs(nuevas["eur"] - tasas_actuales["eur"]) > 0.0001
        or nuevas["fecha"] != tasas_actuales["fecha"]
    )

def texto_variacion_usd():
    if tasas_actuales["usd"] is None or tasas_actuales.get("usd_anterior") is None:
        return None
    actual = tasa_oficial_usd()
    anterior = redondear(tasas_actuales["usd_anterior"])
    if anterior == 0:
        return None
    diferencia = redondear(actual - anterior)
    porcentaje = redondear(((actual - anterior) / anterior) * 100)
    if diferencia > 0:
        return (
            f"📈 *Aumento:* {formatear_numero(diferencia)} Bs\n"
            f"📊 *Variación:* +{formatear_numero(porcentaje)}%"
        )
    if diferencia < 0:
        return (
            f"📉 *Bajada:* {formatear_numero(abs(diferencia))} Bs\n"
            f"📊 *Variación:* {formatear_numero(porcentaje)}%"
        )
    return "➖ *Sin variación* respecto a la tasa anterior"

def obtener_promedio_p2p(trade_type, reintentos=2):
    url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
    payload = {
        "asset": "USDT",
        "fiat": "VES",
        "merchantCheck": False,
        "page": 1,
        "rows": 10,
        "tradeType": trade_type,
        "transAmount": "",
        "payTypes": [],
        "publisherType": None,
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "clienttype": "web",
    }

    for intento in range(reintentos):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=3.5)
            r.raise_for_status()
            data = r.json()
            anuncios = data.get("data") or []
            if len(anuncios) < 3:
                if intento < reintentos - 1:
                    time.sleep(0.4)
                    continue
                return None

            fin = min(7, len(anuncios))
            precios = []
            for i in range(1, fin):
                try:
                    precios.append(float(anuncios[i]["adv"]["price"]))
                except (KeyError, ValueError, TypeError):
                    continue

            if not precios:
                if intento < reintentos - 1:
                    time.sleep(0.4)
                    continue
                return None
            return sum(precios) / len(precios)

        except Exception as e:
            print(f"Error P2P {trade_type} (intento {intento + 1}): {e}")
            if intento < reintentos - 1:
                time.sleep(0.4)
    return None

def obtener_usdt_paralelo():
    with ThreadPoolExecutor(max_workers=2) as executor:
        fut_compra = executor.submit(obtener_promedio_p2p, "BUY")
        fut_venta = executor.submit(obtener_promedio_p2p, "SELL")
        compra = fut_compra.result()
        venta = fut_venta.result()
    return compra, venta

def tasa_oficial_usd():
    return redondear(tasas_actuales["usd"])

def tasa_oficial_eur():
    return redondear(tasas_actuales["eur"])

def tasa_intervencion_usd():
    return redondear(tasa_oficial_usd() * (1 + COMISION_INTERVENCION))

def tasa_menudeo_usd():
    return redondear(tasa_oficial_usd() * (1 + COMISION_MENUDEO))

def tasa_intervencion_eur():
    return redondear(tasa_oficial_eur() * (1 + COMISION_INTERVENCION))

def tasa_menudeo_eur():
    return redondear(tasa_oficial_eur() * (1 + COMISION_MENUDEO))

def _fuente(size, bold=False):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(base_dir, ARCHIVO_FUENTE)
    if os.path.exists(local):
        return ImageFont.truetype(local, size)

    candidatos = [
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for ruta in candidatos:
        if os.path.exists(ruta):
            return ImageFont.truetype(ruta, size)
    return ImageFont.load_default()

def generar_imagen_tasa():
    if not os.path.exists(ARCHIVO_PLANTILLA):
        raise FileNotFoundError("No encuentro plantilla.jpg en la carpeta del bot")

    base = Image.open(ARCHIVO_PLANTILLA).convert("RGB")
    sx = base.width / 896
    sy = base.height / 1195
    draw = ImageDraw.Draw(base)

    def caja(x1, y1, x2, y2):
        return (int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy))

    caja_usd = caja(280, 620, 810, 765)
    caja_eur = caja(280, 800, 810, 940)
    caja_fecha = caja(90, 965, 810, 1055)

    h_monto = caja_usd[3] - caja_usd[1]
    h_fecha = caja_fecha[3] - caja_fecha[1]

    fuente_monto = _fuente(max(int(h_monto * 0.75), 96), True)
    fuente_fecha = _fuente(max(int(h_fecha * 0.50), 52), True)
    color_texto = (72, 22, 28)

    def texto_centro(text, box, font, fill):
        x1, y1, x2, y2 = box
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        while tw > (x2 - x1) - 16 and font.size > 40:
            font = _fuente(font.size - 2, True)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = x1 + (x2 - x1 - tw) // 2
        y = y1 + (y2 - y1 - th) // 2 - 2
        draw.text((x, y), text, font=font, fill=fill)

    texto_centro(
        f"{formatear_numero(tasa_oficial_usd())} Bs",
        caja_usd,
        fuente_monto,
        color_texto,
    )
    texto_centro(
        f"{formatear_numero(tasa_oficial_eur())} Bs",
        caja_eur,
        fuente_monto,
        color_texto,
    )
    texto_centro(
        fecha_imagen(tasas_actuales["fecha"]),
        caja_fecha,
        fuente_fecha,
        color_texto,
    )

    bio = BytesIO()
    base.save(bio, format="PNG")
    bio.seek(0)
    return bio

def texto_tasas_canal():
    base = (
        f"*BANCA & DIVISA*\n\n"
        f"*Tasa oficial del Banco Central*\n"
        f"{fecha_espanol(tasas_actuales['fecha'])}\n\n"
        f"Dólar (USD)   {formatear_numero(tasa_oficial_usd())} Bs\n"
        f"Euro  (EUR)   {formatear_numero(tasa_oficial_eur())} Bs"
    )
    var = texto_variacion_usd()
    if var:
        base += f"\n\n{var}"
    base += (
        f"\n\n@BancaYDivisaVe\n"
        f"¡La información, a tu alcance!"
    )
    return base

async def publicar_en_canal(bot, tambien_privado=False):
    if tasas_actuales["usd"] is None:
        return False
    try:
        foto = generar_imagen_tasa()
        await bot.send_photo(
            chat_id=CANAL,
            photo=InputFile(foto, filename="tasa_bcv.png"),
            caption=texto_tasas_canal(),
            parse_mode="Markdown"
        )
        if tambien_privado:
            foto2 = generar_imagen_tasa()
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=InputFile(foto2, filename="tasa_bcv.png"),
                caption="Vista previa (también se publicó en el canal)",
                parse_mode="Markdown"
            )
        return True
    except Exception as e:
        print(f"Error publicando en el canal: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = (
        "*BANCA & CALCULOS*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Calculadora de tasas BCV\n"
        "Uso en el canal principal y grupos autorizados.\n\n"
        "*Comandos disponibles:*\n"
        "`/usd 10` — Dólares → bolívares\n"
        "`/eur 10` — Euros → bolívares\n"
        "`/bs 10000` — Bolívares → dólares\n"
        "`/tasas` — Tasas del día\n\n"
        "Escribe el comando y el monto."
    )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def comando_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return
    jobs = context.job_queue.get_jobs_by_name("busqueda_tasa")
    if jobs:
        await update.message.reply_text("La búsqueda ya está activa. Revisaré cada 30 segundos.")
        return
    context.job_queue.run_repeating(
        tarea_buscar,
        interval=INTERVALO_BUSQUEDA,
        first=5,
        name="busqueda_tasa"
    )
    await update.message.reply_text(
        "Búsqueda activada.\nRevisaré cada *30 segundos*.\nCuando detecte una tasa nueva, la publicaré *una sola vez* en el canal y me detendré.",
        parse_mode="Markdown"
    )

async def tarea_buscar(context: ContextTypes.DEFAULT_TYPE):
    nuevas = consultar_tasas()
    if not nuevas:
        return
    if not tasas_cambiaron(nuevas):
        return

    aplicar_nuevas_tasas(nuevas)

    ok = await publicar_en_canal(context.bot)
    for job in context.job_queue.get_jobs_by_name("busqueda_tasa"):
        job.schedule_removal()

    if ok:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="Nueva tasa detectada y publicada en el canal.\nBúsqueda desactivada."
        )
    else:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text="Tasa actualizada, pero no pude publicar en el canal.\nRevisa permisos del bot y plantilla.jpg."
        )

async def reload_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return
    mensaje_espera = await update.message.reply_text("Actualizando tasas desde el BCV...")
    nuevas = consultar_tasas()
    if not nuevas:
        await mensaje_espera.edit_text("No se pudieron actualizar las tasas. Intenta más tarde.")
        return

    cambio = tasas_cambiaron(nuevas)
    aplicar_nuevas_tasas(nuevas)

    texto = (
        f"Tasas actualizadas (BCV oficial)\n\n"
        f"USD: {formatear_numero(tasa_oficial_usd())} Bs\n"
        f"EUR: {formatear_numero(tasa_oficial_eur())} Bs\n\n"
        f"Fecha valor: {fecha_espanol(tasas_actuales['fecha'])}"
    )
    var = texto_variacion_usd()
    if var and cambio:
        texto += "\n\n" + var.replace("*", "")
    if cambio:
        ok = await publicar_en_canal(context.bot)
        texto += "\n\nSe publicó en el canal." if ok else "\n\nNo se pudo publicar en el canal."
    else:
        texto += "\n\nLa tasa no cambió. No se publicó nada en el canal."

    await mensaje_espera.edit_text(texto)

async def comando_montos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id):
        return
    if tasas_actuales["usd"] is None:
        await update.message.reply_text("Las tasas no están disponibles por ahora.")
        return

    espera = await update.message.reply_text("Enviando mensajes al canal...")

    bcv = tasa_oficial_usd()
    intervencion = tasa_intervencion_usd()

    mensaje1 = (
        "🔔 *TASA DE INTERVENCIÓN* 📌\n\n"
        "🖥 *Tasa de Intervención Bancaria.* 💱\n\n"
        "📊 *Fórmula y resultado:*\n"
        f"( {formatear_numero(bcv)} ) + 0,50% = *Tasa ( {formatear_numero(intervencion)} )*\n\n"
        "*@BancaYDivisaVe* ✅\n"
        "🔗 *¡La información, a tu alcance!*"
    )

    lineas = ["USD ($)       VES (Bs.)"]
    for monto in range(50, 1001, 50):
        total = redondear(monto * intervencion)
        usd_txt = f"${monto}"
        if monto >= 1000:
            usd_txt = f"${monto:,}".replace(",", ".")
        ves_txt = formatear_numero(total)
        lineas.append(f"{usd_txt:<14}{ves_txt}")
    tabla = "\n".join(lineas)

    mensaje2 = (
        "🔔 *MONTOS* 📌\n\n"
        f"*Montos de compra calculados con La Tasa De Intervención "
        f"{formatear_numero(intervencion)} Bs.* 💱\n\n"
        f"```\n{tabla}\n```\n\n"
        "*@BancaYDivisaVe* ✅\n"
        "🔗 *¡La información, a tu alcance!*"
    )

    try:
        await context.bot.send_message(chat_id=CANAL, text=mensaje1, parse_mode="Markdown")
        await asyncio.sleep(5)
        await context.bot.send_message(chat_id=CANAL, text=mensaje2, parse_mode="Markdown")
        await espera.edit_text("Mensajes enviados al canal @BancaYDivisaVe.")
    except Exception as e:
        print(f"Error en /montos: {e}")
        await espera.edit_text(
            "No pude publicar en el canal.\n"
            "Revisa que el bot sea administrador y pueda enviar mensajes."
        )

async def comando_usd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if tasas_actuales["usd"] is None:
        await update.message.reply_text("Las tasas no están disponibles por ahora. Intenta más tarde.")
        return
    if not context.args:
        await update.message.reply_text("Formato correcto:\n`/usd 10`", parse_mode="Markdown")
        return
    try:
        monto = float(context.args[0].replace(",", "."))
        if monto <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Monto no válido.\nEjemplo: `/usd 10`", parse_mode="Markdown")
        return

    tasa = tasa_oficial_usd()
    tasa_id = tasa_intervencion_usd()
    tasa_men = tasa_menudeo_usd()
    respuesta = (
        f"*BANCA & CALCULOS*\n\n"
        f"*Tasa oficial USD*\n{formatear_numero(tasa)} Bs\n\n"
        f"*Monto solicitado*\n{formatear_numero(monto)} USD\n\n"
        f"────────────────\n"
        f"*BCV (sin comisión)*\n"
        f"{formatear_numero(tasa)} → {formatear_numero(monto * tasa)} Bs\n\n"
        f"*Intervención 0,5%*\n"
        f"{formatear_numero(tasa_id)} → {formatear_numero(monto * tasa_id)} Bs\n\n"
        f"*Menudeo 1,2%*\n"
        f"{formatear_numero(tasa_men)} → {formatear_numero(monto * tasa_men)} Bs\n"
        f"────────────────\n\n"
        f"{fecha_espanol()}"
    )
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def comando_eur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if tasas_actuales["eur"] is None:
        await update.message.reply_text("Las tasas no están disponibles por ahora. Intenta más tarde.")
        return
    if not context.args:
        await update.message.reply_text("Formato correcto:\n`/eur 10`", parse_mode="Markdown")
        return
    try:
        monto = float(context.args[0].replace(",", "."))
        if monto <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Monto no válido.\nEjemplo: `/eur 10`", parse_mode="Markdown")
        return

    tasa = tasa_oficial_eur()
    tasa_id = tasa_intervencion_eur()
    tasa_men = tasa_menudeo_eur()
    respuesta = (
        f"*BANCA & CALCULOS*\n\n"
        f"*Tasa oficial EUR*\n{formatear_numero(tasa)} Bs\n\n"
        f"*Monto solicitado*\n{formatear_numero(monto)} EUR\n\n"
        f"────────────────\n"
        f"*BCV (sin comisión)*\n"
        f"{formatear_numero(tasa)} → {formatear_numero(monto * tasa)} Bs\n\n"
        f"*Intervención 0,5%*\n"
        f"{formatear_numero(tasa_id)} → {formatear_numero(monto * tasa_id)} Bs\n\n"
        f"*Menudeo 1,2%*\n"
        f"{formatear_numero(tasa_men)} → {formatear_numero(monto * tasa_men)} Bs\n"
        f"────────────────\n\n"
        f"{fecha_espanol()}"
    )
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def comando_bs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if tasas_actuales["usd"] is None:
        await update.message.reply_text("Las tasas no están disponibles por ahora. Intenta más tarde.")
        return
    if not context.args:
        await update.message.reply_text("Formato correcto:\n`/bs 10000`", parse_mode="Markdown")
        return
    try:
        monto = float(context.args[0].replace(",", "."))
        if monto <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Monto no válido.\nEjemplo: `/bs 10000`", parse_mode="Markdown")
        return

    tasa = tasa_oficial_usd()
    tasa_id = tasa_intervencion_usd()
    tasa_men = tasa_menudeo_usd()
    respuesta = (
        f"*BANCA & CALCULOS*\n\n"
        f"*Tasa oficial USD*\n{formatear_numero(tasa)} Bs\n\n"
        f"*Monto solicitado*\n{formatear_numero(monto)} BS\n\n"
        f"────────────────\n"
        f"*BCV (sin comisión)*\n"
        f"{formatear_numero(tasa)} → {formatear_numero(monto / tasa)} USD\n\n"
        f"*Intervención 0,5%*\n"
        f"{formatear_numero(tasa_id)} → {formatear_numero(monto / tasa_id)} USD\n\n"
        f"*Menudeo 1,2%*\n"
        f"{formatear_numero(tasa_men)} → {formatear_numero(monto / tasa_men)} USD\n"
        f"────────────────\n\n"
        f"{fecha_espanol()}"
    )
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def comando_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ahora = time.time()

    if cache_usdt["compra"] and (ahora - cache_usdt["timestamp"]) < CACHE_USDT_SEGUNDOS:
        compra = cache_usdt["compra"]
        venta = cache_usdt["venta"]
    else:
        mensaje_espera = await update.message.reply_text("Consultando Binance P2P...")
        compra, venta = await asyncio.get_event_loop().run_in_executor(
            None, obtener_usdt_paralelo
        )

        if compra is None or venta is None:
            await asyncio.sleep(0.5)
            compra, venta = await asyncio.get_event_loop().run_in_executor(
                None, obtener_usdt_paralelo
            )

        if compra is None or venta is None:
            await mensaje_espera.edit_text(
                "No se pudieron obtener las tasas de Binance P2P. Intenta más tarde."
            )
            return

        cache_usdt["compra"] = compra
        cache_usdt["venta"] = venta
        cache_usdt["timestamp"] = ahora
        await mensaje_espera.delete()

    intervencion = tasa_intervencion_usd() if tasas_actuales["usd"] else None
    if intervencion and intervencion > 0:
        brecha = ((venta - intervencion) / intervencion) * 100
        if brecha >= 0:
            bloque_brecha = f"*Brecha:* {formatear_numero(abs(brecha))}% por encima"
        else:
            bloque_brecha = f"*Brecha:* {formatear_numero(abs(brecha))}% por debajo"
    else:
        bloque_brecha = "*Brecha:* no disponible"

    respuesta = (
        f"*BANCA & CRIPTOS | INFORMA*\n\n"
        f"*Fecha:* {datetime.now().strftime('%d/%m')}\n\n"
        f"Compra  →  {formatear_numero(compra)} VES\n"
        f"Venta   →  {formatear_numero(venta)} VES\n\n"
        f"{bloque_brecha}\n\n"
        f"@BancaYDivisaVe\n"
        f"¡La información, a tu alcance!"
    )
    await update.message.reply_text(respuesta, parse_mode="Markdown")

async def comando_tasas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if tasas_actuales["usd"] is None:
        await update.message.reply_text("Las tasas no están disponibles por ahora. Intenta más tarde.")
        return

    respuesta = (
        f"*BANCA & DIVISA*\n\n"
        f"*Tasa oficial del Banco Central*\n"
        f"{fecha_espanol(tasas_actuales['fecha'])}\n\n"
        f"Dólar (USD)   {formatear_numero(tasa_oficial_usd())} Bs\n"
        f"Euro  (EUR)   {formatear_numero(tasa_oficial_eur())} Bs"
    )
    var = texto_variacion_usd()
    if var:
        respuesta += f"\n\n{var}"
    respuesta += (
        f"\n\n@BancaYDivisaVe\n"
        f"¡La información, a tu alcance!"
    )
    await update.message.reply_text(respuesta, parse_mode="Markdown")

def main():
    cargar_tasas()
    if tasas_actuales["usd"] is None:
        actualizar_tasas()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("usd", comando_usd))
    application.add_handler(CommandHandler("eur", comando_eur))
    application.add_handler(CommandHandler("bs", comando_bs))
    application.add_handler(CommandHandler("tasas", comando_tasas))
    application.add_handler(CommandHandler("usdt", comando_usdt))
    application.add_handler(CommandHandler("reload", reload_bot))
    application.add_handler(CommandHandler("buscar", comando_buscar))
    application.add_handler(CommandHandler("montos", comando_montos))

    from flask import Flask
    import threading

    flask_app = Flask(__name__)

    @flask_app.route("/")
    def home():
        return "Bot activo", 200

    @flask_app.route("/keep-alive")
    def keep_alive():
        return "ok", 200

    def run_flask():
        port = int(os.environ.get("PORT", 10000))
        flask_app.run(host="0.0.0.0", port=port)

    threading.Thread(target=run_flask, daemon=True).start()

    print("Bot iniciado correctamente...")
    application.run_polling()

if __name__ == "__main__":
    main()