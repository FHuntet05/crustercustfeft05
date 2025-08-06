import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.utils import sanitize_filename, escape_html
from src.helpers.keyboards import build_panel_keyboard, build_processing_menu
# --- Importamos la función de manejo de texto ---
from . import processing_handler 

logger = logging.getLogger(__name__)

# --- Handler para los comandos principales ---
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
            "• <b>Envíe un archivo</b> o <b>pegue un enlace</b>.\n"
            "• Use /panel para ver su mesa de trabajo."
        )
        await message.reply(start_message, parse_mode=ParseMode.HTML)

    elif command == "panel":
        pending_tasks = await db_instance.get_pending_tasks(user.id)
        if not pending_tasks:
            return await message.reply("✅ ¡Su mesa de trabajo está vacía!", parse_mode=ParseMode.HTML)
        
        keyboard = build_panel_keyboard(pending_tasks)
        await message.reply("📋 <b>Su mesa de trabajo actual:</b>", reply_markup=keyboard, parse_mode=ParseMode.HTML)

# --- Handler para archivos y URLs ---
@Client.on_message(filters.private & (filters.document | filters.audio | filters.video | filters.regex(r"https?://\S+")))
async def media_handler(client: Client, message: Message):
    user = message.from_user
    
    # Si es un archivo
    if message.media:
        file = getattr(message, message.media.value)
        file_type = message.media.value.lower()
        
        if file.file_size > 4000 * 1024 * 1024:
            return await message.reply_text("😕 Lo siento, no puedo procesar archivos de más de 4GB.")
        
        task_id = await db_instance.add_task(
            user_id=user.id, file_type=file_type,
            file_name=sanitize_filename(getattr(file, 'file_name', "Archivo Sin Nombre")),
            file_size=file.file_size, file_id=file.file_id, message_id=message.id
        )
        reply_text = f"✅ He recibido <code>{escape_html(sanitize_filename(getattr(file, 'file_name', 'archivo')))}</code>"
    # Si es una URL
    else:
        # Aquí iría la lógica de downloader.get_url_info que ya teníamos
        # Por ahora, un placeholder:
        task_id = await db_instance.add_task(user_id=user.id, file_type='video', url=message.text)
        reply_text = f"✅ He recibido el enlace <code>{escape_html(message.text)}</code>"

    if task_id:
        await message.reply(
            f"{reply_text} y lo he añadido a su mesa de trabajo.\n\n"
            "Use /panel para ver y procesar sus tareas.",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.reply("❌ Hubo un error al registrar la tarea.")

# --- Handler para texto (delegación) ---
@Client.on_message(filters.private & filters.text & ~filters.command())
async def text_handler(client: Client, message: Message):
    """
    Este handler se activa con cualquier mensaje de texto que no sea un comando.
    Delega la lógica al processing_handler si el usuario está en medio de una configuración.
    """
    if hasattr(client, 'user_data') and message.from_user.id in client.user_data:
        await processing_handler.handle_text_input_for_config(client, message)
    else:
        # Opcional: responder si el texto no se espera
        await message.reply("No entiendo ese comando. Envíe un archivo, un enlace o use /start.")

# --- Handlers para los callbacks de botones ---
@Client.on_callback_query(filters.regex(r"^task_process_"))
async def on_task_process(client: Client, query: CallbackQuery):
    await query.answer()
    task_id = query.data.split("_")[2]
    task = await db_instance.get_task(task_id)
    if not task:
        return await query.message.edit_text("❌ Error: La tarea ya no existe.")
    
    keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
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