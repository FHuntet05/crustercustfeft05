# --- Comandos para Canales Restringidos ---

from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from src.helpers.utils import escape_html
from src.db.mongo_manager import db_instance

@Client.on_message(filters.command("add_channel") & filters.private)
async def add_channel_command(client: Client, message: Message):
    """Comando para añadir un canal restringido."""
    user_id = message.from_user.id
    
    # Verificar si hay argumentos
    if len(message.command) < 2:
        await message.reply(
            "📝 <b>Uso del comando:</b>\n"
            "/add_channel <code>enlace_o_id</code>\n\n"
            "El enlace puede ser:\n"
            "• Enlace de invitación\n"
            "• Username del canal\n"
            "• ID numérico del canal",
            parse_mode=ParseMode.HTML
        )
        return

    channel_identifier = message.command[1]
    status_msg = await message.reply("🔄 Verificando acceso al canal...")

    from src.core.userbot_handler import userbot_handler
    if not await userbot_handler.check_rate_limit(user_id):
        await status_msg.edit_text(
            "⚠️ <b>Rate Limit:</b> Debe esperar 5 minutos entre usos del userbot.\n"
            "Esto es para evitar problemas con Telegram.",
            parse_mode=ParseMode.HTML
        )
        return

    channel_id, channel_title = await userbot_handler.validate_and_get_channel(channel_identifier)
    
    if not channel_id:
        await status_msg.edit_text(
            "❌ <b>Error:</b> No se pudo acceder al canal.\n"
            "Asegúrese de que:\n"
            "• El enlace/ID es correcto\n"
            "• La cuenta del userbot es miembro del canal\n"
            "• El canal existe y está disponible",
            parse_mode=ParseMode.HTML
        )
        return

    if await db_instance.add_restricted_channel(user_id, channel_id, channel_title):
        await status_msg.edit_text(
            f"✅ <b>Canal registrado exitosamente:</b>\n"
            f"• Nombre: <code>{escape_html(channel_title)}</code>\n"
            f"• ID: <code>{channel_id}</code>\n\n"
            f"Para descargar contenido use:\n"
            f"<code>/get_restricted {channel_id} mensaje_id</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        await status_msg.edit_text("❌ Error al registrar el canal en la base de datos.")

@Client.on_message(filters.command("list_channels") & filters.private)
async def list_channels_command(client: Client, message: Message):
    """Lista los canales restringidos registrados."""
    user_id = message.from_user.id
    channels = await db_instance.get_restricted_channels(user_id)
    
    if not channels:
        await message.reply(
            "📋 <b>Canales Registrados:</b>\n"
            "No tiene canales registrados.\n\n"
            "Use /add_channel para añadir uno.",
            parse_mode=ParseMode.HTML
        )
        return

    response = ["📋 <b>Canales Registrados:</b>"]
    for channel_id, info in channels.items():
        response.append(
            f"• {escape_html(info['title'])}\n"
            f"  ID: <code>{channel_id}</code>"
        )
    response.append("\nUse /get_restricted <code>canal_id mensaje_id</code> para descargar contenido.")
    
    await message.reply("\n".join(response), parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("get_restricted") & filters.private)
async def get_restricted_command(client: Client, message: Message):
    """Descarga contenido de un canal restringido."""
    user_id = message.from_user.id
    
    if len(message.command) != 3:
        await message.reply(
            "📝 <b>Uso del comando:</b>\n"
            "/get_restricted <code>canal_id mensaje_id</code>\n\n"
            "Ejemplo:\n"
            "/get_restricted -1001234567890 123",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        channel_id = int(message.command[1])
        message_id = int(message.command[2])
    except ValueError:
        await message.reply("❌ El canal_id y mensaje_id deben ser números.")
        return

    channels = await db_instance.get_restricted_channels(user_id)
    if str(channel_id) not in channels:
        await message.reply(
            "❌ Canal no registrado.\n"
            "Use /list_channels para ver sus canales disponibles.",
            parse_mode=ParseMode.HTML
        )
        return

    status_msg = await message.reply("🔄 Obteniendo información del mensaje...")

    from src.core.userbot_handler import userbot_handler
    if not await userbot_handler.check_rate_limit(user_id):
        await status_msg.edit_text(
            "⚠️ <b>Rate Limit:</b> Debe esperar 5 minutos entre usos del userbot.",
            parse_mode=ParseMode.HTML
        )
        return

    # Primero obtener información del mensaje
    message_info = await userbot_handler.get_message_info(channel_id, message_id)
    if not message_info:
        await status_msg.edit_text(
            "❌ No se encontró contenido descargable en ese mensaje.\n"
            "Asegúrese de que:\n"
            "• El mensaje existe\n"
            "• Contiene un archivo multimedia\n"
            "• El userbot tiene acceso al mensaje",
            parse_mode=ParseMode.HTML
        )
        return

    # Crear tarea para procesar el archivo
    task_id = await db_instance.add_task(
        user_id=user_id,
        file_type=message_info['type'],
        file_name=message_info.get('file_name', f"restricted_{message_id}"),
        file_id=message_info['file_id'],
        status="pending_processing",
        metadata={
            "size": message_info.get('file_size', 0),
            "duration": message_info.get('duration', 0),
            "resolution": f"{message_info.get('width', 0)}x{message_info.get('height', 0)}" if message_info.get('width') else None
        }
    )

    if task_id:
        await status_msg.edit_text(
            "✅ <b>Archivo añadido al panel</b>\n"
            f"Tipo: {message_info['type']}\n"
            f"Nombre: <code>{escape_html(message_info.get('file_name', 'Desconocido'))}</code>\n\n"
            "Use /panel para ver y configurar la tarea.",
            parse_mode=ParseMode.HTML
        )
    else:
        await status_msg.edit_text("❌ Error al crear la tarea en la base de datos.")