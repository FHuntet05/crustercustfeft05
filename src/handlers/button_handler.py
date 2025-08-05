import logging
from telegram import Update
from telegram.ext import ContextTypes
from src.db.mongo_manager import db_instance
from bson.objectid import ObjectId # Para convertir el string de vuelta a un ID de MongoDB

logger = logging.getLogger(__name__)

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja todas las pulsaciones de botones inline."""
    query = update.callback_query
    
    # Es buena práctica responder al callback inmediatamente.
    # Esto le dice a Telegram "Recibido", y el ícono de carga en el botón del usuario desaparece.
    await query.answer()

    # 'query.data' contiene el callback_data que definimos (ej. "delete_60b8d3b8f8a8d3b8f8a8d3b8")
    data = query.data
    
    # Usamos partition para dividir el string. Es más seguro que split si no hay '_'.
    # ej: "delete_123" -> action="delete", task_id="123"
    # ej: "process_all" -> action="process_all", task_id=""
    action, _, task_id = data.partition('_')
    
    user_id = query.from_user.id
    
    if action == "delete":
        try:
            # Convertir el string del ID de vuelta a un ObjectId de MongoDB
            obj_id = ObjectId(task_id)
            # Borrar la tarea de la base de datos, asegurándonos de que pertenece al usuario
            delete_result = db_instance.tasks.delete_one({"_id": obj_id, "user_id": user_id})
            
            if delete_result.deleted_count > 0:
                await query.edit_message_text(text=f"🗑️ Tarea descartada con éxito.")
                logger.info(f"Tarea {task_id} borrada por el usuario {user_id}")
            else:
                await query.edit_message_text(text="❌ No se encontró la tarea o ya fue eliminada.")
                
        except Exception as e:
            logger.error(f"Error al intentar borrar la tarea {task_id}: {e}")
            await query.edit_message_text(text="❌ Ocurrió un error al intentar descartar la tarea.")

    elif action == "process":
        # Por ahora, solo confirmamos la acción. Aquí irá el menú de procesamiento.
        await query.edit_message_text(text=f"🎬 ¡Entendido, Jefe! Preparando el menú de procesamiento para la tarea {task_id}...")
        
    elif data == "delete_all":
        # Borra todas las tareas del usuario que estén pendientes de revisión
        delete_result = db_instance.tasks.delete_many({"user_id": user_id, "status": "pending_review"})
        await query.edit_message_text(text=f"💥 Limpieza completada. Se descartaron {delete_result.deleted_count} tareas.")
        
    elif data == "process_all":
        # Por ahora, solo confirmamos. Aquí irá la lógica del modo bulk.
        await query.edit_message_text(text="✨ ¡Entendido, Jefe! Añadiendo todas las tareas a la cola con el perfil automático...")
    
    else:
        # Fallback por si llega un callback desconocido
        await query.edit_message_text(text="🤔 Acción desconocida o no implementada todavía.")