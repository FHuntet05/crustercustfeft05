import logging
import os
import asyncio
from dotenv import load_dotenv

from pyrogram import Client, __version__
from pyrogram.raw.all import layer

# Carga de Entorno
load_dotenv()

# --- Configuración del Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", mode='a', encoding='utf-8')
    ]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Importación de Módulos del Proyecto ---
from src.core import worker # Importamos nuestro worker
from src.db.mongo_manager import db_instance # Importamos la instancia de la DB

class MediaSuiteBot(Client):
    def __init__(self):
        # Leemos las variables desde el entorno
        self.api_id = os.getenv("API_ID")
        self.api_hash = os.getenv("API_HASH")
        self.bot_token = os.getenv("TELEGRAM_TOKEN")
        self.admin_id = int(os.getenv("ADMIN_USER_ID", 0))

        # Verificaciones críticas
        if not all([self.api_id, self.api_hash, self.bot_token]):
            logger.critical("¡ERROR CRÍTICO! Faltan API_ID, API_HASH o TELEGRAM_TOKEN en el .env")
            exit(1)

        super().__init__(
            name="MediaSuiteBot",
            api_id=int(self.api_id),
            api_hash=self.api_hash,
            bot_token=self.bot_token,
            workers=200,  # Alto número de workers para concurrencia
            plugins={"root": "src/plugins"}, # Pyrogram carga los handlers desde esta carpeta
            sleep_threshold=15,
        )

    async def start(self):
        await super().start()
        
        # Guardar información útil del bot
        me = await self.get_me()
        self.mention = me.mention
        self.username = me.username
        logger.info(f"Bot {me.first_name} iniciado. Versión de Pyrogram: {__version__} (Layer {layer})")
        
        # Ping a la DB
        try:
            await db_instance.client.admin.command('ping')
            logger.info("Conexión con MongoDB Atlas establecida.")
        except Exception as e:
            logger.critical(f"¡ERROR CRÍTICO! No se pudo conectar a MongoDB al iniciar. Error: {e}")
            exit(1)

        # Lanzar nuestro worker de procesamiento de tareas en segundo plano
        logger.info("Lanzando el worker de procesamiento de tareas...")
        asyncio.create_task(worker.worker_loop(self)) # Le pasamos el propio bot al worker
        
        # Notificar al admin que el bot ha iniciado
        if self.admin_id:
            try:
                await self.send_message(self.admin_id, f"✅ **Bot reiniciado con éxito**\n\nEstoy listo para procesar tareas.")
            except Exception as e:
                logger.warning(f"No se pudo enviar el mensaje de inicio al admin ({self.admin_id}). Error: {e}")

    async def stop(self):
        await super().stop()
        logger.info("Bot detenido correctamente.")

if __name__ == "__main__":
    MediaSuiteBot().run()