import logging
import os
from telegram import Update
from telegram.ext import ContextTypes

from src.db.mongo_manager import db_instance
from src.helpers.keyboards import build_panel_keyboard
from src.helpers.utils import get_greeting, escape_html

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /start."""
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    
    await update.message.reply_html(
        f"¡A sus órdenes, {greeting_prefix}Bienvenido!\n\n"
        "Soy su Asistente de Medios personal. "
        "Envíeme un archivo, un enlace o use /panel para ver su mesa de trabajo."
    )

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /panel, que ahora maneja tanto comandos como callbacks."""
    # Determinar si la actualización viene de un comando o de un botón
    if update.effective_message and not update.callback_query:
        # Viene de un comando /panel
        user = update.effective_user
        message_to_interact_with = update.effective_message
        is_callback = False
    elif update.callback_query:
        # Viene de una pulsación de botón (ej. "Volver al Panel")
        user = update.callback_query.from_user
        message_to_interact_with = update.callback_query.message
        is_callback = True
    else:
        # Caso improbable, no hacer nada
        return

    greeting_prefix = get_greeting(user.id)
    
    pending_tasks = db_instance.get_pending_tasks(user.id)
    
    if not pending_tasks:
        text = f"✅ ¡{greeting_prefix}su mesa de trabajo está vacía!"
        if is_callback:
            await message_to_interact_with.edit_text(text, parse_mode='HTML')
        else:
            await message_to_interact_with.reply_html(text)
        return
        
    keyboard = build_panel_keyboard(pending_tasks)
    
    response_text = f"📋 <b>{greeting_prefix}su mesa de trabajo actual:</b>"
    
    # Usamos el mensaje apropiado para responder o editar
    if is_callback:
        await message_to_interact_with.edit_text(response_text, reply_markup=keyboard, parse_mode='HTML')
    else:
        await message_to_interact_with.reply_html(response_text, reply_markup=keyboard)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador para el comando /settings (placeholder)."""
    user = update.effective_user
    greeting_prefix = get_greeting(user.id)
    
    await update.message.reply_html(
        f"⚙️ {greeting_prefix}este es el panel de configuración.\n\n"
        "<i>(Función no implementada todavía)</i>"
    )

async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador de errores para loggear excepciones."""
    logger.error(f"Error: {context.error}", exc_info=context.error)
    
    # Notificar al usuario (si es posible)
    if update and update.effective_message:
        try:
            greeting_prefix = get_greeting(update.effective_user.id)
            await update.effective_message.reply_html(
                f"❌ Lo siento, {greeting_prefix}ha ocurrido un error inesperado.\n"
                "El incidente ha sido registrado."
            )
        except Exception as e:
            logger.error(f"No se pudo enviar el mensaje de error al usuario: {e}")