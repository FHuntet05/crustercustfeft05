import asyncio
import shutil
import logging
import os

from src.core.exceptions import DiskSpaceError

logger = logging.getLogger(__name__)

CPU_INTENSIVE_TASKS_LIMIT = int(os.getenv("CPU_INTENSIVE_TASKS_LIMIT", "2"))
DISK_USAGE_LIMIT_PERCENT = int(os.getenv("DISK_USAGE_LIMIT_PERCENT", "95"))

class ResourceManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ResourceManager, cls).__new__(cls)
            cls._instance.ffmpeg_semaphore = asyncio.Semaphore(CPU_INTENSIVE_TASKS_LIMIT)
            logger.info(f"Gestor de Recursos inicializado. Límite de tareas FFmpeg concurrentes: {CPU_INTENSIVE_TASKS_LIMIT}")
        return cls._instance

    async def acquire_ffmpeg_slot(self):
        logger.info("Esperando por un slot de procesamiento FFmpeg...")
        await self.ffmpeg_semaphore.acquire()
        logger.info("Slot de FFmpeg adquirido. El procesamiento puede comenzar.")

    def release_ffmpeg_slot(self):
        self.ffmpeg_semaphore.release()
        logger.info("Slot de FFmpeg liberado.")

    def check_disk_space(self, required_space_bytes: int = 0):
        total, used, free = shutil.disk_usage('.')
        usage_percent = (used / total) * 100

        logger.info(f"Comprobación de disco: {usage_percent:.2f}% usado. Espacio libre: {free / (1024**3):.2f} GB.")
        
        if usage_percent > DISK_USAGE_LIMIT_PERCENT:
            raise DiskSpaceError(f"El uso del disco ({usage_percent:.2f}%) supera el límite del {DISK_USAGE_LIMIT_PERCENT}%.")
        
        if free < required_space_bytes:
            raise DiskSpaceError(f"Espacio libre insuficiente. Se requieren {required_space_bytes / (1024**2):.2f} MB pero solo hay {free / (1024**2):.2f} MB disponibles.")

resource_manager = ResourceManager()