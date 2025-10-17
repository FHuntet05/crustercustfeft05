import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime

from pyrogram import Client
from pyrogram.types import Message, CallbackQuery
from pyrogram.enums import ParseMode

from src.db.mongo_manager import db_instance
from src.helpers.messages import BotMessages
from src.helpers.keyboards import build_cancel_button
from src.core.task_processor import TaskProcessor

logger = logging.getLogger(__name__)

class ProcessingManager:
    def __init__(self):
        self.active_tasks: Dict[str, Dict[str, Any]] = {}
        
    async def start_task(self, client: Client, task_id: str, chat_id: int, message_id: int) -> None:
        """Inicia una tarea de procesamiento"""
        if task_id in self.active_tasks:
            logger.warning(f"Task {task_id} already active")
            return
            
        task = await db_instance.get_task(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return
            
        self.active_tasks[task_id] = {
            'chat_id': chat_id,
            'message_id': message_id,
            'start_time': datetime.now(),
            'status': 'processing',
            'cancel_requested': False
        }
        
        try:
            processor = TaskProcessor(task, client)
            await processor.process_task()
        except Exception as e:
            logger.error(f"Error processing task {task_id}: {e}")
            await self.update_task_status(client, task_id, "❌ Error en el procesamiento", str(e))
        finally:
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]
                
    async def cancel_task(self, task_id: str) -> bool:
        """Solicita la cancelación de una tarea"""
        if task_id not in self.active_tasks:
            return False
            
        self.active_tasks[task_id]['cancel_requested'] = True
        return True
        
    async def update_task_status(self, client: Client, task_id: str, status: str, details: Optional[str] = None) -> None:
        """Actualiza el estado de una tarea y su mensaje en Telegram"""
        if task_id not in self.active_tasks:
            return
            
        task_info = self.active_tasks[task_id]
        try:
            keyboard = build_cancel_button(task_id) if status == "processing" else None
            await client.edit_message_text(
                chat_id=task_info['chat_id'],
                message_id=task_info['message_id'],
                text=f"{status}\n{details if details else ''}",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error updating task status: {e}")
            
    def is_task_active(self, task_id: str) -> bool:
        """Verifica si una tarea está activa"""
        return task_id in self.active_tasks
        
    def should_cancel_task(self, task_id: str) -> bool:
        """Verifica si se solicitó cancelar una tarea"""
        if task_id not in self.active_tasks:
            return False
        return self.active_tasks[task_id]['cancel_requested']
        
    async def update_progress(self, client: Client, task_id: str, current: int, total: int, action: str) -> None:
        """Actualiza el progreso de una tarea"""
        if task_id not in self.active_tasks:
            return
            
        task_info = self.active_tasks[task_id]
        now = datetime.now()
        elapsed = (now - task_info['start_time']).total_seconds()
        
        if elapsed == 0:
            return
            
        speed = current / elapsed
        eta = (total - current) / speed if speed > 0 else 0
        
        progress_msg = BotMessages.processing_status(current, total, action, speed, eta, elapsed)
        
        try:
            keyboard = build_cancel_button(task_id)
            await client.edit_message_text(
                chat_id=task_info['chat_id'],
                message_id=task_info['message_id'],
                text=progress_msg,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.debug(f"Error updating progress: {e}")

processing_manager = ProcessingManager()