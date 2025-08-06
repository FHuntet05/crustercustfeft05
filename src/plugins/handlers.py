# src/plugins/handlers.py

import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode
from datetime import datetime

from src.db.mongo_manager import db_instance
from src.core import downloader
from src.helpers.utils import sanitize_filename, escape_html
from src.helpers.keyboards import build_panel_keyboard, build_processing_menu, build_download_quality_menu, build_search_results_keyboard
from . import processing_handler 

logger = logging.getLogger(__name__)

@Client.on_message(filters.command(["start", "panel"]) & filters.private)
async def main_commands(client: Client, message: Message):
    command = message.command[0].lower()
    user = message.from_user

    if command == "start":
        await db_instance.get_user_settings(user.id)
        start_message = (
            f"A sus órdenes, bienvenido a la <b>Suite de Medios</b>.\n\n"
            "Soy su Asistente personal. Estoy listo para procesar sus archivos.\n\n"
            "<b>¿Cómo empezar?</b>\n"
            "• <b>Envíe un archivo</b> (video, audio, etc).\n"
            "• <b>Pegue un enlace</b> de YouTube, etc.\n"
            "• <b>Escriba un texto</b> para buscar música.\n"
            "• Use /panel para ver su mesa de trabajo."
        )
        await message.reply(start_message, parse_mode=ParseMode.HTML)

    elif command == "panel":
        pending_tasks = await db_instance.get_pending_tasks(user.id)
        if not pending_tasks:
            return await message.reply("✅ ¡Su mesa de trabajo está vacía!", parse_mode=ParseMode.HTML)
        
        keyboard = build_panel_keyboard(pending_tasks)
        await message.reply("📋 <b>Su mesa de trabajo actual:</b>", reply_markup=keyboard, parse_mode=ParseMode.HTML)

@Client.on_message(filters.private & (filters.document | filters.audio | filters.video | filters.regex(r"https?://\S+")))
async def media_handler(client: Client, message: Message):
    user = message.from_user
    
    if message.media:
        file = getattr(message, message.media.value)
        file_type = message.media.value.lower()
        
        if hasattr(file, 'file_size') and file.file_size > 4000 * 1024 * 1024:
            return await message.reply_text("😕 Lo siento, no puedo procesar archivos de más de 4GB.")
        
        task_id = await db_instance.add_task(
            user_id=user.id, file_type=file_type,
            file_name=sanitize_filename(getattr(file, 'file_name', "Archivo Sin Nombre")),
            file_size=file.file_size, file_id=file.file_id, message_id=message.id
        )
        reply_text = f"✅ He recibido <code>{escape_html(sanitize_filename(getattr(file, 'file_name', 'archivo')))}</code>"
        
        if task_id:
            await message.reply(
                f"{reply_text} y lo he añadido a su mesa de trabajo.\n\n"
                "Use /panel para ver y procesar sus tareas.",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply("❌ Hubo un error al registrar la tarea.")

    else: # Es una URL
        status_msg = await message.reply("🔎 Analizando enlace...", parse_mode=ParseMode.HTML)
        info = downloader.get_url_info(message.text)

        if not info:
            return await status_msg.edit("❌ No pude obtener información de ese enlace.")

        task_id = await db_instance.add_task(
            user_id=user.id,
            file_type='video' if info.get('is_video') else 'audio',
            url=info['url'],
            file_name=sanitize_filename(info['title']),
            url_info=info
        )

        if not task_id:
            return await status_msg.edit("❌ Error al crear la tarea en la base de datos.")

        keyboard = build_download_quality_menu(str(task_id), info['formats'])
        text = (
            f"✅ Enlace analizado:\n\n"
            f"<b>Título:</b> {escape_html(info['title'])}\n"
            f"<b>Canal:</b> {escape_html(info['uploader'])}\n\n"
            "Seleccione la calidad que desea descargar:"
        )
        await status_msg.edit(text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


@Client.on_message(filters.private & filters.text)
async def text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if hasattr(client, 'user_data') and user_id in client.user_data:
        # Esto es una respuesta a una configuración (ej. renombrar)
        await processing_handler.handle_text_input_for_config(client, message)
    elif not message.command:
        # Esto es texto libre, lo tratamos como una búsqueda de música
        query = message.text.strip()
        status_msg = await message.reply(f"🔎 Buscando música: <code>{escape_html(query)}</code>...", parse_mode=ParseMode.HTML)
        
        search_results = downloader.search_music(query, limit=5)
        if not search_results:
            return await status_msg.edit("❌ No encontré resultados para su búsqueda.")

        docs_to_insert = []
        for res in search_results:
            res['user_id'] = user_id
            res['created_at'] = datetime.utcnow()
            docs_to_insert.append(res)
        
        result = await db_instance.search_results.insert_many(docs_to_insert)
        
        # Añadir los ObjectIds a los resultados para construir el teclado
        for i, res_id in enumerate(result.inserted_ids):
            search_results[i]['_id'] = str(res_id)
            
        keyboard = build_search_results_keyboard(search_results)
        await status_msg.edit(
            "✅ He encontrado esto. Seleccione una para descargar:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )


@Client.on_callback_query(filters.regex(r"^task_process_"))
async def on_task_process(client: Client, query: CallbackQuery):
    await query.answer()
    task_id = query.data.split("_")[2]
    task = await db_instance.get_task(task_id)
    if not task:
        return await query.message.edit_text("❌ Error: La tarea ya no existe.")
    
    keyboard = build_processing_menu(task_id, task['file_type'], task, task.get('original_filename', ''))
    await query.message.edit_text(
        f"🛠️ ¿Qué desea hacer con:\n<code>{escape_html(task.get('original_filename', '...'))}</code>?", 
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^task_queuesingle_"))
async def on_queue_single(client: Client, query: CallbackQuery):
    await query.answer()
    task_id = query.data.split("_")[2]
    await db_instance.update_task(task_id, "status", "queued")
    await query.message.edit_text("🔥 Tarea enviada a la forja. El procesamiento comenzará en breve.")

@Client.on_callback_query(filters.regex(r"^panel_show"))
async def on_panel_show(client: Client, query: CallbackQuery):
    await query.answer()
    pending_tasks = await db_instance.get_pending_tasks(query.from_user.id)
    if not pending_tasks:
        return await query.message.edit_text("✅ ¡Su mesa de trabajo está vacía!", parse_mode=ParseMode.HTML)
    
    keyboard = build_panel_keyboard(pending_tasks)
    await query.message.edit_text("📋 <b>Su mesa de trabajo actual:</b>", reply_markup=keyboard, parse_mode=ParseMode.HTML)