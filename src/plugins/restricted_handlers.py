# --- Comandos para Canales Restringidos ---

import logging
import asyncio
from typing import Optional, Dict

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait

from src.helpers.utils import escape_html, format_bytes, format_time
from src.db.mongo_manager import db_instance
from src.core.channel_joiner import channel_joiner
from src.core.restricted_handler import restricted_handler

logger = logging.getLogger(__name__)

# Comando para añadir un canal
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
    
    # Si no se proporciona enlace, mostrar instrucciones
    if len(message.text.split()) == 1:
        await message.reply(
            "� <b>Descarga de Contenido Restringido</b>\n\n"
            "Por favor, envía el enlace del mensaje que quieres descargar.\n\n"
            "<b>Formatos válidos:</b>\n"
            "• Mensaje específico: https://t.me/canal/123\n"
            "• Canal privado: https://t.me/+abc123...\n"
            "• Canal público: https://t.me/nombre_canal",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❓ Ayuda", callback_data="help_restricted")]
            ])
        )
        return

    # Obtener enlace del mensaje
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return await message.reply(
            "❌ Por favor, proporciona el enlace del mensaje a descargar.\n"
            "Ejemplo: /get_restricted https://t.me/canal/123",
            parse_mode=ParseMode.HTML
        )

    link = args[1].strip()
    status_msg = await message.reply("🔄 Procesando enlace...")

    # Procesar el enlace
    success, status_text, data = await restricted_handler.process_message_link(
        client, link,
        progress_callback=lambda current, total: _show_progress(
            status_msg, "Descargando", current, total
        )
    )

    if not success:
        await status_msg.edit_text(status_text, parse_mode=ParseMode.HTML)
        return

    # Extraer información
    msg = data['message']
    media_info = data['media_info']

    # Preparar y mostrar información del archivo
    info_text = (
        f"📁 <b>Archivo encontrado:</b>\n\n"
        f"💾 Tamaño: {format_bytes(media_info['file_size'])}\n"
    )

    if media_info['duration']:
        info_text += f"⏱ Duración: {format_time(media_info['duration'])}\n"
    if media_info['width'] and media_info['height']:
        info_text += f"📐 Resolución: {media_info['width']}x{media_info['height']}\n"

    info_text += "\n¿Deseas descargar este archivo?"

    # Mostrar confirmación con botones
    await status_msg.edit_text(
        info_text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Descargar", callback_data=f"dl_restricted_{msg.chat.id}_{msg.id}"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancel_restricted")
            ]
        ])
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