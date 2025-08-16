# --- START OF FILE src/core/resource_manager.py ---

import asyncio
import shutil
import logging
import os

from src.core.exceptions import DiskSpaceError

logger = logging.getLogger(__name__)

# Valores por defecto seguros para un VPS de bajos recursos
CPU_INTENSIVE_TASKS_LIMIT = int(os.getenv("CPU_INTENSIVE_TASKS_LIMIT", "1"))
DISK_USAGE_LIMIT_PERCENT = int(os.getenv("DISK_USAGE_LIMIT_PERCENT", "90"))

class ResourceManager:
    """
    Clase Singleton para gestionar los recursos del sistema, como slots de CPU
    para FFmpeg y comprobaciones de espacio en disco. Esencial para la estabilidad en un VPS.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ResourceManager, cls).__new__(cls)
            # El semáforo limita el número de tareas de FFmpeg que se pueden ejecutar simultáneamente.
            cls._instance.ffmpeg_semaphore = asyncio.Semaphore(CPU_INTENSIVE_TASKS_LIMIT)
            logger.info(
                f"Gestor de Recursos inicializado. Límite de tareas FFmpeg concurrentes: {CPU_INTENSIVE_TASKS_LIMIT}"
            )
        return cls._instance

    async def acquire_ffmpeg_slot(self):
        """
        Espera y adquiere un slot libre para procesar una tarea con FFmpeg.
        Esto bloquea la ejecución si todos los slots están ocupados.
        """
        logger.info("Esperando por un slot de procesamiento FFmpeg...")
        await self.ffmpeg_semaphore.acquire()
        active_tasks = CPU_INTENSIVE_TASKS_LIMIT - self.ffmpeg_semaphore._value
        logger.info(f"Slot de FFmpeg adquirido. Tareas activas: {active_tasks}/{CPU_INTENSIVE_TASKS_LIMIT}")

    def release_ffmpeg_slot(self):
        """Libera un slot de FFmpeg, permitiendo que otra tarea en espera comience."""
        self.ffmpeg_semaphore.release()
        active_tasks = CPU_INTENSIVE_TASKS_LIMIT - self.ffmpeg_semaphore._value - 1
        logger.info(f"Slot de FFmpeg liberado. Tareas activas: {active_tasks}/{CPU_INTENSIVE_TASKS_LIMIT}")

    def check_disk_space(self, required_space_bytes: int = 0):
        """
        Verifica si hay suficiente espacio en disco. Lanza DiskSpaceError si se superan los límites.
        """
        try:
            total, used, free = shutil.disk_usage('.')
        except FileNotFoundError:
            logger.error("No se pudo obtener el uso del disco. Asumiendo que hay espacio suficiente.")
            return

        usage_percent = (used / total) * 100
        logger.info(f"Comprobación de disco: {usage_percent:.2f}% usado. Espacio libre: {free / (1024**3):.2f} GB.")
        
        if usage_percent > DISK_USAGE_LIMIT_PERCENT:
            raise DiskSpaceError(
                f"El uso del disco ({usage_percent:.2f}%) supera el límite del {DISK_USAGE_LIMIT_PERCENT}%. "
                "No se procesarán nuevas tareas hasta que se libere espacio."
            )
        
        # Comprobar si el espacio libre es suficiente para el archivo que se va a descargar/procesar (con un margen).
        if free < required_space_bytes * 1.2: # Añadimos un 20% de margen de seguridad
            raise DiskSpaceError(
                f"Espacio libre insuficiente. Se requieren ~{required_space_bytes / (1024**2):.2f} MB pero solo hay "
                f"{free / (1024**2):.2f} MB disponibles."
            )

# Instancia singleton para ser usada en todo el proyecto.
resource_manager = ResourceManager()