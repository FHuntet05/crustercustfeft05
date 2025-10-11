import logging
from datetime import datetime
from typing import Union
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode

from src.core.admin_manager import AdminManager
from src.helpers.utils import escape_html
from src.db.mongo_manager import db_instance

logger = logging.getLogger(__name__)
admin_manager = AdminManager()

def admin_only(func):
    """Decorador para restringir comandos solo a administradores."""
    async def wrapper(client: Client, update: Union[Message, CallbackQuery], *args, **kwargs):
        user_id = update.from_user.id
        admins = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
        
        if user_id not in admins:
            if isinstance(update, CallbackQuery):
                await update.answer("⛔ Solo administradores", show_alert=True)
            else:
                await update.reply("⛔ Este comando es solo para administradores.")
            return
        return await func(client, update, *args, **kwargs)
    return wrapper

@Client.on_message(filters.command("ban") & filters.private)
@admin_only
async def ban_command(client: Client, message: Message):
    """Banea a un usuario del bot."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply(
            "❌ <b>Uso:</b> /ban <code>user_id</code> [razón]",
            parse_mode=ParseMode.HTML
        )
        return
        
    try:
        user_id = int(parts[1])
        reason = " ".join(parts[2:]) if len(parts) > 2 else "No especificada"
        
        # Verificar si ya está baneado
        is_banned, ban_info = await admin_manager.is_user_banned(user_id)
        if is_banned:
            await message.reply(
                f"⚠️ El usuario {user_id} ya está baneado.\n"
                f"<b>Razón:</b> {escape_html(ban_info.get('reason', 'No especificada'))}\n"
                f"<b>Fecha:</b> {ban_info.get('banned_at', 'Desconocida')}",
                parse_mode=ParseMode.HTML
            )
            return
            
        # Ejecutar baneo
        if await admin_manager.ban_user(user_id, reason, message.from_user.id):
            await message.reply(
                f"✅ Usuario {user_id} baneado exitosamente.\n"
                f"<b>Razón:</b> {escape_html(reason)}",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply("❌ Error al banear usuario.")
            
    except ValueError:
        await message.reply("❌ El ID de usuario debe ser un número.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

@Client.on_message(filters.command("unban") & filters.private)
@admin_only
async def unban_command(client: Client, message: Message):
    """Desbanea a un usuario del bot."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply(
            "❌ <b>Uso:</b> /unban <code>user_id</code>",
            parse_mode=ParseMode.HTML
        )
        return
        
    try:
        user_id = int(parts[1])
        
        # Verificar si está baneado
        is_banned, _ = await admin_manager.is_user_banned(user_id)
        if not is_banned:
            await message.reply(f"⚠️ El usuario {user_id} no está baneado.")
            return
            
        # Ejecutar desbaneo
        if await admin_manager.unban_user(user_id, message.from_user.id):
            await message.reply(f"✅ Usuario {user_id} desbaneado exitosamente.")
        else:
            await message.reply("❌ Error al desbanear usuario.")
            
    except ValueError:
        await message.reply("❌ El ID de usuario debe ser un número.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

@Client.on_message(filters.command("stats") & filters.private)
@admin_only
async def stats_command(client: Client, message: Message):
    """Muestra estadísticas generales del bot."""
    stats = await admin_manager.get_user_stats()
    
    if not stats:
        await message.reply("❌ Error al obtener estadísticas.")
        return
        
    # Formatear top usuarios
    top_users_text = ""
    for i, user in enumerate(stats.get("top_users", []), 1):
        top_users_text += f"{i}. ID: <code>{user['_id']}</code> - {user['total']} tareas\n"
        
    stats_text = (
        "📊 <b>Estadísticas del Bot</b>\n\n"
        f"👥 <b>Usuarios:</b>\n"
        f"• Total: {stats['total_users']}\n"
        f"• Baneados: {stats['banned_users']}\n"
        f"• Activos hoy: {stats['active_today']}\n\n"
        f"📝 <b>Tareas:</b>\n"
        f"• Total: {stats['total_tasks']}\n"
        f"• Completadas: {stats['tasks_completed']}\n"
        f"• Fallidas: {stats['tasks_failed']}\n"
        f"• Tasa de éxito: {(stats['tasks_completed']/stats['total_tasks']*100):.1f}%\n\n"
        f"📺 <b>Canales:</b>\n"
        f"• Monitoreados: {stats['monitored_channels']}\n\n"
        f"🏆 <b>Top 5 Usuarios:</b>\n{top_users_text}"
    )
    
    await message.reply(stats_text, parse_mode=ParseMode.HTML)

@Client.on_message(filters.command("user") & filters.private)
@admin_only
async def user_info_command(client: Client, message: Message):
    """Muestra información detallada de un usuario."""
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply(
            "❌ <b>Uso:</b> /user <code>user_id</code>",
            parse_mode=ParseMode.HTML
        )
        return
        
    try:
        user_id = int(parts[1])
        user_details = await admin_manager.get_user_details(user_id)
        
        if not user_details:
            await message.reply("❌ Error al obtener detalles del usuario.")
            return
            
        # Formatear historial de desbaneos
        unban_history = ""
        for unban in user_details.get("unban_history", []):
            unban_history += (
                f"• Por: <code>{unban.get('unbanned_by', 'Desconocido')}</code>\n"
                f"  Fecha: {unban.get('unbanned_at', 'Desconocida')}\n"
            )
            
        details_text = (
            f"👤 <b>Información del Usuario {user_id}</b>\n\n"
            f"📅 <b>Fechas:</b>\n"
            f"• Primer uso: {user_details['first_seen'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"• Última actividad: {user_details['last_active'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"📊 <b>Actividad:</b>\n"
            f"• Tareas totales: {user_details['total_tasks']}\n"
            f"• Completadas: {user_details['completed_tasks']}\n"
            f"• Fallidas: {user_details['failed_tasks']}\n"
            f"• Canales monitoreados: {user_details['monitored_channels']}\n\n"
        )
        
        # Añadir información de ban si está baneado
        if user_details['banned']:
            ban_info = user_details.get('ban_info', {})
            details_text += (
                f"🚫 <b>Estado: BANEADO</b>\n"
                f"• Razón: {escape_html(ban_info.get('reason', 'No especificada'))}\n"
                f"• Fecha: {ban_info.get('banned_at', 'Desconocida')}\n"
                f"• Por: <code>{ban_info.get('banned_by', 'Desconocido')}</code>\n\n"
            )
            
        # Añadir historial de desbaneos si existe
        if unban_history:
            details_text += f"📜 <b>Historial de Desbaneos:</b>\n{unban_history}"
            
        await message.reply(details_text, parse_mode=ParseMode.HTML)
        
    except ValueError:
        await message.reply("❌ El ID de usuario debe ser un número.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

# Middleware para verificar baneos
@Client.on_message(group=-2)
async def ban_check_middleware(client: Client, message: Message):
    """Verifica si un usuario está baneado antes de procesar cualquier comando."""
    if not message.from_user:
        return
        
    user_id = message.from_user.id
    
    # No verificar baneos para administradores
    admins = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
    if user_id in admins:
        return
        
    # Verificar si está baneado
    is_banned, ban_info = await admin_manager.is_user_banned(user_id)
    if is_banned:
        reason = ban_info.get('reason', 'No especificada') if ban_info else 'No especificada'
        await message.reply(
            f"⛔ <b>Acceso denegado</b>\n"
            f"Has sido baneado del bot.\n"
            f"<b>Razón:</b> {escape_html(reason)}",
            parse_mode=ParseMode.HTML
        )
        raise StopPropagation
        
    # Actualizar última actividad del usuario
    await db_instance.user_settings.update_one(
        {"_id": user_id},
        {"$set": {"last_active": datetime.utcnow()}},
        upsert=True
    )