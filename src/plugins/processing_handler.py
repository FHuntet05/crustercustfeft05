# src/plugins/processing_handler.py

import logging
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
from datetime import datetime
from math import ceil

from src.db.mongo_manager import db
from src.core import downloader
from src.helpers import keyboards

logger = logging.getLogger(__name__)


@Client.on_callback_query(filters.regex(r"^process_"))
async def on_process_button(client: Client, query: CallbackQuery):
    """Maneja los clics en los botones del menú de procesamiento."""
    try:
        await query.answer()
        parts = query.data.split("_")
        action = parts[1]
        task_id = parts[2]
        
        message_text = ""
        update_operation = None

        if action == "trim":
            update_operation = "trim"
            message_text = (
                "✂️ **Cortar Archivo**\n\n"
                "Por favor, envía el tiempo de inicio y fin en el formato `HH:MM:SS-HH:MM:SS`.\n\n"
                "Ejemplo: `00:01:15-00:02:30`"
            )
        elif action == "split":
            update_operation = "split"
            message_text = (
                "📏 **Dividir Video**\n\n"
                "¿Cómo quieres dividirlo?\n"
                " - Envía la **duración de cada segmento** en segundos (ej: `60`)\n"
                " - O envía el **número de partes** (ej: `3 partes`)"
            )
        elif action == "gif":
            update_operation = "gif"
            message_text = (
                "✨ **Crear GIF**\n\n"
                "Por favor, envía el intervalo de tiempo para el GIF en formato `HH:MM:SS-HH:MM:SS`.\n\n"
                "Ejemplo: `00:00:05-00:00:10`"
            )
        else:
            await query.answer(f"La función '{action}' aún no está implementada.", show_alert=True)
            return

        if update_operation and message_text:
            await db.update_task(task_id, {"status": "awaiting_input", "operation": update_operation})
            await query.message.edit_text(message_text, reply_markup=None)

    except Exception as e:
        logger.error(f"Error en on_process_button: {e}", exc_info=True)
        await query.answer("Ocurrió un error al procesar esta acción.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^search_select_"))
async def on_music_search_select(client: Client, query: CallbackQuery):
    """Inicia el flujo de descarga automática de música."""
    await query.answer("Procesando tu selección...")
    video_id = query.data.split("_")[-1]
    url = f"https://www.youtube.com/watch?v={video_id}"
    media_info = await downloader.get_media_info(url)
    if not media_info:
        await query.edit_message_text("❌ No se pudo obtener información para esa selección.")
        return

    audio_formats = [f for f in media_info.get('formats', []) if f.get('vcodec') == 'none']
    if not audio_formats:
        await query.edit_message_text("❌ No se encontró un formato de solo audio.")
        return
    best_audio = sorted(audio_formats, key=lambda x: x.get('abr', 0), reverse=True)[0]

    task_data = {
        "user_id": query.from_user.id, "chat_id": query.message.chat.id,
        "url": media_info["webpage_url"], "title": media_info.get("title", "N/A"),
        "thumbnail_url": media_info.get("thumbnail"), "status": "queued",
        "selected_format_id": best_audio["format_id"], "file_type": "audio",
        "download_lyrics": True, "embed_thumbnail": True, "created_at": datetime.utcnow()
    }
    await db.create_task(task_data)
    await query.edit_message_text(f"✅ **¡En la cola!**\n\n'{media_info['title']}' se está procesando.", reply_markup=None)

@Client.on_callback_query(filters.regex(r"^format_"))
async def on_format_select(client: Client, query: CallbackQuery):
    """Maneja la selección de formato para descargas de enlaces."""
    await query.answer("Formato recibido.")
    parts = query.data.split("_")
    format_id, task_id = parts[1], parts[2]
    await db.update_task(task_id, {"selected_format_id": format_id, "status": "queued"})
    task = await db.get_task(task_id)
    if task:
        await query.edit_message_text(f"✅ **¡En la cola!**\n\n'{task['title']}' se procesará en breve.", reply_markup=None)

@Client.on_callback_query(filters.regex(r"^sp_")) # Search Pagination
async def on_search_pagination(client: Client, query: CallbackQuery):
    """Manejador para la paginación de los resultados de búsqueda."""
    try:
        await query.answer()
        parts = query.data.split("_")
        page = int(parts[1])
        query_id = parts[2]

        session = await db.get_search_session(query_id)
        if not session:
            await query.edit_message_text("⚠️ Tu sesión de búsqueda ha expirado. Por favor, realiza la búsqueda de nuevo.")
            return
        
        results = session['results']
        items_per_page = 5
        total_pages = ceil(len(results) / items_per_page)
        
        start = (page - 1) * items_per_page
        end = start + items_per_page
        
        keyboard = keyboards.create_search_results_keyboard(results[start:end], page, total_pages, query_id)
        
        await query.edit_message_reply_markup(reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error en la paginación de búsqueda: {e}", exc_info=True)
        await query.message.edit_text("❌ Ocurrió un error al cambiar de página.")