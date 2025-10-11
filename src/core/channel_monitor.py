from pyrogram import Client
from typing import List, Dict, Optional
import asyncio
import logging
from datetime import datetime
from ..db.mongo_manager import MongoManager
from .exceptions import ChannelMonitorError
from .worker import Worker

logger = logging.getLogger(__name__)

class ChannelMonitor:
    def __init__(self, client: Client, mongo: MongoManager, worker: Worker):
        self.client = client
        self.mongo = mongo
        self.worker = worker
        self.monitoring_tasks: Dict[int, asyncio.Task] = {}
        
    async def add_monitored_channel(self, channel_id: int, user_id: int) -> bool:
        """Añade un canal para monitoreo continuo"""
        try:
            # Verificar si ya existe
            if await self.mongo.is_channel_monitored(channel_id, user_id):
                return False
                
            # Intentar unirse al canal si no está unido
            try:
                chat = await self.client.get_chat(channel_id)
                if not chat.is_restricted:
                    return False
            except Exception as e:
                logger.error(f"Error al verificar canal {channel_id}: {str(e)}")
                return False
                
            # Registrar en la base de datos
            await self.mongo.add_monitored_channel(channel_id, user_id)
            
            # Iniciar tarea de monitoreo
            self.start_monitoring(channel_id, user_id)
            return True
            
        except Exception as e:
            logger.error(f"Error al añadir canal monitoreado: {str(e)}")
            return False
            
    def start_monitoring(self, channel_id: int, user_id: int):
        """Inicia el monitoreo de un canal"""
        if channel_id not in self.monitoring_tasks:
            task = asyncio.create_task(self._monitor_channel(channel_id, user_id))
            self.monitoring_tasks[channel_id] = task
            
    async def stop_monitoring(self, channel_id: int):
        """Detiene el monitoreo de un canal"""
        if channel_id in self.monitoring_tasks:
            self.monitoring_tasks[channel_id].cancel()
            del self.monitoring_tasks[channel_id]
            
    async def _monitor_channel(self, channel_id: int, user_id: int):
        """Monitorea un canal continuamente"""
        last_message_id = 0
        while True:
            try:
                # Obtener últimos mensajes
                messages = await self.client.get_chat_history(
                    chat_id=channel_id,
                    limit=100,
                    offset_id=last_message_id
                )
                
                for message in messages:
                    if message.id > last_message_id:
                        last_message_id = message.id
                        
                        # Procesar si contiene media
                        if message.media:
                            await self.worker.process_restricted_media(
                                message,
                                user_id,
                                is_monitored=True
                            )
                
                # Esperar intervalo antes de siguiente check
                await asyncio.sleep(300)  # 5 minutos
                
            except Exception as e:
                logger.error(f"Error monitoreando canal {channel_id}: {str(e)}")
                await asyncio.sleep(600)  # 10 minutos en caso de error
                
    async def list_monitored_channels(self, user_id: int) -> List[Dict]:
        """Lista los canales monitoreados de un usuario"""
        try:
            channels = await self.mongo.get_monitored_channels(user_id)
            result = []
            
            for channel in channels:
                try:
                    chat = await self.client.get_chat(channel["channel_id"])
                    result.append({
                        "channel_id": channel["channel_id"],
                        "title": chat.title,
                        "username": chat.username,
                        "added_on": channel["added_on"]
                    })
                except Exception:
                    # Si no se puede obtener info del canal, usar datos básicos
                    result.append({
                        "channel_id": channel["channel_id"],
                        "title": "Canal no disponible",
                        "username": None,
                        "added_on": channel["added_on"]
                    })
                    
            return result
            
        except Exception as e:
            logger.error(f"Error listando canales monitoreados: {str(e)}")
            return []

    async def remove_monitored_channel(self, channel_id: int, user_id: int) -> bool:
        """Elimina un canal de monitoreo"""
        try:
            await self.stop_monitoring(channel_id)
            return await self.mongo.remove_monitored_channel(channel_id, user_id)
        except Exception as e:
            logger.error(f"Error eliminando canal monitoreado: {str(e)}")
            return False