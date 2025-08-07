# src/plugins/processing_handler.py

import logging
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery
from datetime import datetime

from src.db.mongo_manager import db
from src.core import downloader
from src.helpers import keyboards, utils

# Configuración del logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@Client.on_callback_query(filters.regex(r"^search_select_"))
async def on_music_search_select(client: Client, query: CallbackQuery):
    """
    Manejador para cuando un usuario selecciona una canción de los resultados de búsqueda.
    Inicia el flujo de descarga automática de audio con carátula y letras.
    """
    try:
        await query.answer("Procesando tu selección...")
        video_id = query.data.split("_")[-1]
        user_id = query.from_user.id
        
        # Obtener información del medio usando el ID de YouTube
        url = f"https://www.youtube.com/watch?v={video_id}"
        media_info = await downloader.get_media_info(url)
        
        if not media_info:
            await query.edit_message_text("❌ Lo siento, no se pudo obtener información para esa selección. Inténtalo de nuevo.")
            return

        # Filtrar para encontrar el mejor formato de solo audio (mayor bitrate)
        audio_formats = [f for f in media_info.get('formats', []) if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
        if not audio_formats:
            await query.edit_message_text("❌ No se encontró un formato de solo audio para este video.")
            return
        
        best_audio = sorted(audio_formats, key=lambda x: x.get('abr', 0), reverse=True)[0]

        # Crear la tarea en la base de datos
        task_data = {
            "user_id": user_id,
            "message_id": query.message.id,
            "chat_id": query.message.chat.id,
            "url": media_info["webpage_url"],
            "title": media_info.get("title", "Título Desconocido"),
            "thumbnail_url": media_info.get("thumbnail"),
            "status": "queued",
            "selected_format_id": best_audio["format_id"],
            "file_type": "audio",
            "download_lyrics": True,   # Flag para descargar letras
            "embed_thumbnail": True, # Flag para incrustar carátula
            "created_at": datetime.utcnow()
        }
        
        task_id = await db.create_task(task_data)
        logger.info(f"Nueva tarea de audio automática creada ({task_id}) para el usuario {user_id}.")

        # Informar al usuario y limpiar el teclado de búsqueda
        await query.edit_message_text(
            f"✅ **¡En la cola!**\n\nTu canción:\n**'{media_info['title']}'**\n\nSe está procesando. Te la enviaré cuando esté lista.",
            reply_markup=None  # Elimina los botones
        )

    except Exception as e:
        logger.error(f"Error en on_music_search_select: {e}", exc_info=True)
        try:
            await query.edit_message_text("❌ Ocurrió un error inesperado al procesar tu solicitud.")
        except Exception as e_edit:
            logger.error(f"No se pudo ni editar el mensaje de error: {e_edit}")

@Client.on_callback_query(filters.regex(r"^format_"))
async def on_format_select(client: Client, query: CallbackQuery):
    """
    Manejador para cuando un usuario selecciona un formato de video/audio de un enlace.
    """
    try:
        await query.answer("Formato recibido.")
        # formato callback: format_{format_id}_{task_id}
        parts = query.data.split("_")
        format_id = parts[1]
        task_id = parts[2]

        await db.update_task(task_id, {
            "selected_format_id": format_id,
            "status": "queued"
        })

        task = await db.get_task(task_id)
        if task:
            await query.edit_message_text(
                f"✅ **¡En la cola!**\n\nTu archivo:\n**'{task['title']}'**\n\nSe está procesando en el formato que elegiste. Te lo enviaré en breve.",
                reply_markup=None
            )
            logger.info(f"Tarea {task_id} encolada por el usuario {query.from_user.id} con formato {format_id}.")
        else:
            await query.edit_message_text("❌ Error: No se encontró la tarea asociada.")
            logger.error(f"No se pudo encontrar la tarea {task_id} después de la selección de formato.")

    except Exception as e:
        logger.error(f"Error en on_format_select: {e}", exc_info=True)
        await query.edit_message_text("❌ Ocurrió un error al seleccionar el formato.")

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
        items_per_page = 5 # Debe coincidir con el valor en handlers.py
        total_pages = (len(results) - 1) // items_per_page + 1
        
        start = (page - 1) * items_per_page
        end = start + items_per_page
        
        keyboard = keyboards.create_search_results_keyboard(results[start:end], page, total_pages, query_id)
        
        await query.edit_message_reply_markup(reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error en la paginación de búsqueda: {e}", exc_info=True)
        await query.message.edit_text("❌ Ocurrió un error al cambiar de página.")

# Placeholder para futuros handlers de procesamiento
@Client.on_callback_query(filters.regex(r"^process_"))
async def on_process_button(client: Client, query: CallbackQuery):
    await query.answer("Esta función aún no está implementada.", show_alert=True)
    logger.info(f"Botón de proceso presionado sin implementación: {query.data}")