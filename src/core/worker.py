import logging
import time
from bson.objectid import ObjectId

from src.db.mongo_manager import db_instance
from . import ffmpeg # Importamos nuestro futuro módulo de ffmpeg

logger = logging.getLogger(__name__)

def find_and_process_task():
    """Busca una tarea en la cola y la procesa."""
    
    # find_one_and_update es una operación atómica.
    # Esto previene que múltiples workers (si los tuviéramos) cojan la misma tarea.
    task = db_instance.tasks.find_one_and_update(
        {"status": "queued"},
        {"$set": {"status": "processing"}},
        sort=[("created_at", 1)] # Procesar la más antigua primero
    )
    
    if task:
        task_id = task.get('_id')
        logger.info(f"[WORKER] Tarea {task_id} recogida de la cola. Iniciando procesamiento.")
        
        try:
            # Aquí llamamos a la función de procesamiento real
            result_path = ffmpeg.process_task_with_ffmpeg(task)
            
            if result_path:
                # Si el procesamiento fue exitoso
                db_instance.update_task_status(task_id, "done")
                # Aquí iría la lógica para enviar el archivo final al usuario.
                logger.info(f"[WORKER] Tarea {task_id} marcada como 'done'.")
            else:
                # Si el procesamiento falló
                db_instance.update_task_status(task_id, "error")
                # Aquí iría la lógica para notificar al usuario del error.
                logger.error(f"[WORKER] Tarea {task_id} marcada como 'error' durante el procesamiento.")

        except Exception as e:
            logger.critical(f"[WORKER] Ha ocurrido un error crítico al procesar la tarea {task_id}: {e}")
            db_instance.update_task_status(task_id, "error")
            
        return True # Se encontró y procesó una tarea
        
    else:
        # No se encontraron tareas en la cola
        return False

def start_worker_loop():
    """
    Bucle principal del worker. Se ejecuta en un hilo separado.
    Busca tareas en la cola a intervalos regulares.
    """
    logger.info("[WORKER] El bucle del worker ha comenzado.")
    while True:
        try:
            # Si se procesó una tarea, busca otra inmediatamente.
            # Si no, espera un poco antes de volver a consultar la DB.
            if not find_and_process_task():
                time.sleep(5) # Espera 5 segundos si la cola está vacía
        except Exception as e:
            logger.critical(f"[WORKER] El bucle del worker ha fallado: {e}")
            time.sleep(30) # Espera más tiempo si hay un error grave