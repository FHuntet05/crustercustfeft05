# src/plugins/handlers.py

import logging
import re
import uuid
from datetime import datetime
from math import ceil

from pyrogram import Client, filters
from pyrogram.types import Message

from src.db.mongo_manager import db
# --- CORRECCIÓN CLAVE: Importar el módulo entero ---
from src.core import downloader
from src.helpers import keyboards, utils

# Configuración del logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Expresión regular para detectar URLs
URL_REGEX = re.compile(
    r'((http|https)://(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}'
    r'(\/[-a-zA-Z0-9()@:%_\+.~#?&//=]*)?)'
)
ITEMS_PER_PAGE = 5

@Client.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """Manejador para el comando /start."""
    await message.reply_text(
        "¡Hola! Soy tu asistente de medios.\n\n"
        "Puedes enviarme:\n"
        "• Un **enlace** de YouTube para descargar video o audio.\n"
        "• El **nombre de una canción** para buscar y descargar música.\n"
        "• Un **archivo de audio o video** para acceder a las herramientas de edición."
    )

@Client.on_message(filters.private & (filters.text | filters.document | filters.video | filters.audio))
async def message_handler(client: Client, message: Message):
    """
    Punto de entrada principal para todos los mensajes.
    Discrimina entre texto, enlaces y archivos.
    """
    user_id = message.from_user.id
    text = message.text or message.caption

    # 1. Si es un comando, lo ignora para que lo manejen otros handlers
    if text and text.startswith('/'):
        # Podríamos añadir un mensaje de "comando no reconocido" aquí si queremos
        return

    # 2. Si es un enlace (Camino B: Interactivo)
    if text and URL_REGEX.search(text):
        url = URL_REGEX.search(text).group(0)
        await handle_link(client, message, url)
    # 3. Si es un archivo
    elif message.media:
        await handle_file(client, message)
    # 4. Si es texto libre (Camino A: Búsqueda de Música)
    elif text:
        await handle_text_search(client, message)

async def handle_link(client: Client, message: Message, url: str):
    """Maneja los mensajes que contienen un enlace."""
    user_id = message.from_user.id
    sent_message = await message.reply_text("🔎 Analizando enlace, por favor espera...")
    
    media_info = await downloader.get_media_info(url)
    if not media_info or not media_info.get('formats'):
        await sent_message.edit_text("❌ No se pudo obtener información del enlace. Asegúrate de que sea un video válido.")
        return

    task_data = {
        "user_id": user_id,
        "message_id": message.id,
        "chat_id": message.chat.id,
        "url": url,
        "title": media_info.get("title", "Título Desconocido"),
        "thumbnail_url": media_info.get("thumbnail"),
        "status": "awaiting_format", # Esperando que el usuario elija
        "file_type": "video" if any(f.get('vcodec') != 'none' for f in media_info['formats']) else "audio",
        "created_at": datetime.utcnow()
    }
    task_id = await db.create_task(task_data)
    
    keyboard = keyboards.create_format_selection_keyboard(media_info['formats'], task_id)
    await sent_message.edit_text(
        f"**Selecciona el formato que deseas descargar para:**\n\n_{media_info['title']}_",
        reply_markup=keyboard
    )
    
async def handle_text_search(client: Client, message: Message):
    """Maneja el texto libre como una búsqueda de música."""
    query = message.text
    sent_message = await message.reply_text(f"🔍 Buscando \"{query}\"...")

    # --- CAMBIO CLAVE: Usar la función importada del módulo ---
    results = await downloader.search_music(query)
    
    if not results:
        await sent_message.edit_text("❌ No se encontraron resultados para tu búsqueda.")
        return

    # Guardar resultados en una sesión de búsqueda temporal en la DB
    query_id = str(uuid.uuid4())
    await db.create_search_session(query_id, results)
    
    total_pages = ceil(len(results) / ITEMS_PER_PAGE)
    
    # Mostrar la primera página de resultados
    keyboard = keyboards.create_search_results_keyboard(
        results[:ITEMS_PER_PAGE], 
        current_page=1, 
        total_pages=total_pages, 
        query_id=query_id
    )
    
    await sent_message.edit_text(
        f"**Resultados para \"{query}\":**\n\nSelecciona una canción para descargarla automáticamente.",
        reply_markup=keyboard
    )

async def handle_file(client: Client, message: Message):
    """Maneja los archivos enviados al bot."""
    # Lógica futura para el panel de control y edición
    await message.reply_text(
        "He recibido tu archivo. Próximamente podrás editarlo.\n\n"
        "Para ver tus tareas pendientes de acción manual, usa el comando `/panel` (aún en desarrollo)."
    )