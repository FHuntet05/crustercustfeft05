import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from src.db.mongo_manager import db_instance
from src.helpers.utils import sanitize_filename
from src.helpers.keyboards import build_panel_keyboard, build_processing_menu

logger = logging.getLogger(__name__)

@Client.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    # --- CAMBIO: Añadir await ---
    await db_instance.get_user_settings(message.from_user.id)
    start_message = (
        f"A sus órdenes, bienvenido a la <b>Suite de Medios</b>.\n\n"
        "Soy su Asistente personal. Estoy listo para procesar sus archivos.\n\n"
        "<b>¿Cómo empezar?</b>\n"
        "• <b>Envíe un archivo</b> o <b>pegue un enlace</b>.\n"
        "• Use /panel para ver su mesa de trabajo."
    )
    await message.reply_html(start_message)

@Client.on_message(filters.private & (filters.document | filters.audio | filters.video))
async def any_file_handler(client: Client, message: Message):
    user = message.from_user
    file = getattr(message, message.media.value)
    file_type = message.media.value.lower()
    
    if file.file_size > 4000 * 1024 * 1024:
         return await message.reply_text("😕 Lo siento, no puedo procesar archivos de más de 4GB.")
    
    # --- CAMBIO: Añadir await ---
    task_id = await db_instance.add_task(
        user_id=user.id,
        file_type=file_type,
        file_name=sanitize_filename(getattr(file, 'file_name', "Archivo Sin Nombre")),
        file_size=file.file_size,
        file_id=file.file_id, 
        message_id=message.id
    )

    if task_id:
        await message.reply_html(
            f"✅ He recibido <code>{sanitize_filename(getattr(file, 'file_name', 'archivo'))}</code> y lo he añadido a su mesa de trabajo.\n\n"
            "Use /panel para ver y procesar sus tareas."
        )
    else:
        await message.reply_html(f"❌ Hubo un error al registrar la tarea.")
        
@Client.on_message(filters.command("panel") & filters.private)
async def panel_command(client: Client, message: Message):
    user = message.from_user
    # --- CAMBIO: Añadir await ---
    pending_tasks = await db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        return await message.reply_html("✅ ¡Su mesa de trabajo está vacía!")
        
    keyboard = build_panel_keyboard(pending_tasks)
    await message.reply_html("📋 <b>Su mesa de trabajo actual:</b>", reply_markup=keyboard)


@Client.on_callback_query(filters.regex(r"^task_process_"))
async def on_task_process(client: Client, query: CallbackQuery):
    task_id = query.data.split("_")[2]
    # --- CAMBIO: Añadir await ---
    task = await db_instance.get_task(task_id)
    if not task:
        return await query.answer("❌ Error: La tarea ya no existe.", show_alert=True)
    
    keyboard = build_processing_menu(task_id, task['file_type'], task.get('processing_config', {}), task.get('original_filename', ''))
    await query.message.edit_text(
        f"🛠️ ¿Qué desea hacer con:\n<code>{sanitize_filename(task.get('original_filename', '...'))}</code>?", 
        reply_markup=keyboard
    )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^task_queuesingle_"))
async def on_queue_single(client: Client, query: CallbackQuery):
    task_id = query.data.split("_")[2]
    # --- CAMBIO: Añadir await ---
    await db_instance.update_task(task_id, "status", "queued")
    await query.message.edit_text("🔥 Tarea enviada a la forja. El procesamiento comenzará en breve.")
    await query.answer()