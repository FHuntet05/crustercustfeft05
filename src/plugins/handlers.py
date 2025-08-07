# src/plugins/handlers.py

import logging
import re
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_panel_keyboard, build_search_results_keyboard, build_download_quality_menu
from src.helpers.utils import get_greeting, escape_html, sanitize_filename
from src.core import downloader
from . import processing_handler # Importar para registrar los handlers de texto

logger = logging.getLogger(__name__)

URL_REGEX = r'(https?://[^\s]+)'

@Client.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    """Manejador para el comando /start. Saluda al usuario y crea su perfil si no existe."""
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    await db_instance.get_user_settings(user.id) # Asegura que el usuario exista en la DB
    start_message = (
        f"A sus órdenes, {greeting_prefix}bienvenido a la <b>Suite de Medios</b>.\n\n"
        "Soy su Asistente personal, Forge. Estoy listo para procesar sus archivos.\n\n"
        "<b>¿Cómo empezar?</b>\n"
        "• <b>Para buscar música:</b> simplemente escriba el nombre.\n"
        "• <b>Envíe un archivo:</b> video, audio o documento.\n"
        "• <b>Pegue un enlace:</b> de YouTube, etc.\n"
        "• <b>Use /panel:</b> para ver su mesa de trabajo y procesar archivos.\n"
    )
    # --- CORRECCIÓN ---
    await message.reply(start_message, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("panel"))
async def panel_command(client: Client, message: Message):
    """Muestra la 'mesa de trabajo' del usuario con todos los archivos pendientes de procesar."""
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    pending_tasks = await db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        text = f"✅ ¡{greeting_prefix}Su mesa de trabajo está vacía!"
        # --- CORRECCIÓN ---
        return await message.reply(text, parse_mode=ParseMode.HTML)
        
    keyboard = build_panel_keyboard(pending_tasks)
    response_text = f"📋 <b>{greeting_prefix}Su mesa de trabajo actual:</b>"
    # --- CORRECCIÓN ---
    await message.reply(response_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

@Client.on_message(filters.media)
async def any_file_handler(client: Client, message: Message):
    """
    Recibe cualquier archivo, crea una tarea en la DB y notifica al usuario.
    """
    user = message.from_user
    greeting_prefix = get_greeting(user.id)
    
    original_media_object, file_type = None, None
    if message.video: original_media_object, file_type = message.video, 'video'
    elif message.audio: original_media_object, file_type = message.audio, 'audio'
    elif message.document: original_media_object, file_type = message.document, 'document'
    
    if not original_media_object:
        logger.warning("any_file_handler recibió un mensaje sin un archivo adjunto procesable.")
        return

    final_file_name = sanitize_filename(getattr(original_media_object, 'file_name', "Archivo Sin Nombre"))
    
    task_id = await db_instance.add_task(
        user_id=user.id,
        file_type=file_type,
        file_name=final_file_name,
        file_id=original_media_object.file_id,
        file_size=original_media_object.file_size
    )

    if task_id:
        response_text = (
            f"✅ {greeting_prefix}He recibido <code>{escape_html(final_file_name)}</code> y lo he añadido a su mesa de trabajo.\n\n"
            "Use /panel para ver y procesar sus tareas."
        )
        # --- CORRECCIÓN ---
        await message.reply(response_text, parse_mode=ParseMode.HTML)
    else:
        # --- CORRECCIÓN ---
        await message.reply(f"❌ {greeting_prefix}Hubo un error al registrar la tarea en la base de datos.", parse_mode=ParseMode.HTML)

@Client.on_message(filters.text)
async def text_handler(client: Client, message: Message):
    """
    Dispatcher de texto:
    1. Ignora comandos.
    2. Si es URL -> Procesa URL.
    3. Si es respuesta a config -> Procesa config.
    4. Si no, es búsqueda de música.
    """
    user = message.from_user
    text = message.text.strip()
    greeting_prefix = get_greeting(user.id)

    if text.startswith('/'):
        return

    url_match = re.search(URL_REGEX, text)
    if url_match:
        url = url_match.group(0)
        # --- CORRECCIÓN ---
        status_message = await message.reply(f"🔎 {greeting_prefix}Analizando enlace...", parse_mode=ParseMode.HTML)
        
        info = downloader.get_url_info(url)
        if not info or not info.get('formats'):
            return await status_message.edit_text(f"❌ {greeting_prefix}No pude obtener información de ese enlace.")

        task_id = await db_instance.add_task(
            user_id=user.id,
            file_type='video' if info['is_video'] else 'audio',
            url=info['url'],
            file_name=sanitize_filename(info['title']),
            url_info=info
        )
        if not task_id:
            return await status_message.edit_text(f"❌ {greeting_prefix}Error al crear la tarea en la DB.")
            
        keyboard = build_download_quality_menu(str(task_id), info['formats'])
        response_text = (f"✅ {greeting_prefix}Enlace analizado:\n\n"
                         f"<b>Título:</b> {escape_html(info['title'])}\n"
                         f"<b>Canal:</b> {escape_html(info.get('uploader', 'N/A'))}\n\n"
                         "Seleccione la calidad que desea descargar:")
        
        return await status_message.edit_text(
            response_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    if not hasattr(client, 'user_data'): client.user_data = {}
    if user.id in client.user_data and 'active_config' in client.user_data[user.id]:
        return await processing_handler.handle_text_input_for_config(client, message)

    query = text
    # --- CORRECCIÓN ---
    status_message = await message.reply(f"🔎 {greeting_prefix}Buscando <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
    
    search_results = downloader.search_music(query, limit=20)
    
    if not search_results:
        return await status_message.edit_text(f"❌ {greeting_prefix}No encontré resultados para su búsqueda.")

    search_id = str((await db_instance.search_sessions.insert_one({
        'user_id': user.id, 'query': query, 'created_at': datetime.utcnow()
    })).inserted_id)

    docs_to_insert = []
    for res in search_results:
        res.update({'search_id': search_id, 'user_id': user.id, 'created_at': datetime.utcnow()})
        docs_to_insert.append(res)
    
    if docs_to_insert:
        await db_instance.search_results.insert_many(docs_to_insert)

    all_results_from_db = await db_instance.search_results.find({"search_id": search_id}).to_list(length=100)

    keyboard = build_search_results_keyboard(all_results_from_db, search_id, page=1)
    
    response_text = f"✅ {greeting_prefix}He encontrado esto. Seleccione una para descargar:"
    await status_message.edit_text(
        response_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )