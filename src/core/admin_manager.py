import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from src.db.mongo_manager import db_instance

logger = logging.getLogger(__name__)

class AdminManager:
    def __init__(self):
        self.db = db_instance
        
    async def ban_user(self, user_id: int, reason: str = None, admin_id: int = None) -> bool:
        """Banea a un usuario del bot."""
        try:
            await self.db.user_settings.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "banned": True,
                        "ban_info": {
                            "reason": reason,
                            "banned_by": admin_id,
                            "banned_at": datetime.utcnow()
                        }
                    }
                },
                upsert=True
            )
            logger.info(f"Usuario {user_id} baneado por {admin_id}. Razón: {reason}")
            return True
        except Exception as e:
            logger.error(f"Error al banear usuario {user_id}: {e}")
            return False
            
    async def unban_user(self, user_id: int, admin_id: int = None) -> bool:
        """Desbanea a un usuario del bot."""
        try:
            result = await self.db.user_settings.update_one(
                {"_id": user_id},
                {
                    "$set": {"banned": False},
                    "$push": {
                        "unban_history": {
                            "unbanned_by": admin_id,
                            "unbanned_at": datetime.utcnow()
                        }
                    }
                }
            )
            logger.info(f"Usuario {user_id} desbaneado por {admin_id}")
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error al desbanear usuario {user_id}: {e}")
            return False
            
    async def is_user_banned(self, user_id: int) -> Tuple[bool, Optional[Dict]]:
        """Verifica si un usuario está baneado y retorna la información del ban."""
        try:
            user_data = await self.db.user_settings.find_one({"_id": user_id})
            if user_data and user_data.get("banned", False):
                return True, user_data.get("ban_info")
            return False, None
        except Exception as e:
            logger.error(f"Error al verificar ban de usuario {user_id}: {e}")
            return False, None
            
    async def get_user_stats(self) -> Dict:
        """Obtiene estadísticas generales de usuarios."""
        try:
            stats = {
                "total_users": await self.db.user_settings.count_documents({}),
                "banned_users": await self.db.user_settings.count_documents({"banned": True}),
                "active_today": await self.db.tasks.count_documents({
                    "created_at": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
                }),
                "total_tasks": await self.db.tasks.count_documents({}),
                "tasks_completed": await self.db.tasks.count_documents({"status": "done"}),
                "tasks_failed": await self.db.tasks.count_documents({"status": "error"}),
                "monitored_channels": await self.db.monitored_channels.count_documents({"active": True})
            }
            
            # Usuarios más activos (top 5)
            top_users = await self.db.tasks.aggregate([
                {"$group": {"_id": "$user_id", "total": {"$sum": 1}}},
                {"$sort": {"total": -1}},
                {"$limit": 5}
            ]).to_list(length=5)
            
            stats["top_users"] = top_users
            
            return stats
        except Exception as e:
            logger.error(f"Error al obtener estadísticas: {e}")
            return {}
            
    async def get_user_details(self, user_id: int) -> Dict:
        """Obtiene detalles específicos de un usuario."""
        try:
            user_data = await self.db.user_settings.find_one({"_id": user_id}) or {}
            user_stats = {
                "total_tasks": await self.db.tasks.count_documents({"user_id": user_id}),
                "completed_tasks": await self.db.tasks.count_documents({"user_id": user_id, "status": "done"}),
                "failed_tasks": await self.db.tasks.count_documents({"user_id": user_id, "status": "error"}),
                "monitored_channels": await self.db.monitored_channels.count_documents(
                    {"user_id": user_id, "active": True}
                ),
                "first_seen": user_data.get("created_at", datetime.utcnow()),
                "last_active": user_data.get("last_active", datetime.utcnow()),
                "banned": user_data.get("banned", False),
                "ban_info": user_data.get("ban_info"),
                "unban_history": user_data.get("unban_history", [])
            }
            return user_stats
        except Exception as e:
            logger.error(f"Error al obtener detalles del usuario {user_id}: {e}")
            return {}