import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from src.db.mongo_manager import db_instance
from src.core.ffmpeg import get_media_info

logger = logging.getLogger(__name__)

class ContentManager:
    def __init__(self):
        self.content_types = {
            "series": {
                "patterns": [
                    r"(?i)S\d{2}E\d{2}",  # S01E01
                    r"(?i)Temporada.*Capitulo",
                    r"(?i)Episode"
                ],
                "watermark": "watermark_series.png",
                "thumbnail_overlay": "series_overlay.png",
                "compression": {
                    "bitrate": "2000k",
                    "preset": "medium",
                    "max_size": 500_000_000  # 500MB
                }
            },
            "movies": {
                "patterns": [
                    r"(?i)(1080p|720p|2160p)",
                    r"(?i)(\.mkv|\.mp4)$",
                    r"(?i)(HDTV|BluRay|WEB-DL)"
                ],
                "watermark": "watermark_movies.png",
                "thumbnail_overlay": "movies_overlay.png",
                "compression": {
                    "bitrate": "4000k",
                    "preset": "slow",
                    "max_size": 2_000_000_000  # 2GB
                }
            },
            "shows": {
                "patterns": [
                    r"(?i)(Show|Reality|TV)",
                    r"(?i)(Episode|Cap[ií]tulo)"
                ],
                "watermark": "watermark_shows.png",
                "thumbnail_overlay": "shows_overlay.png",
                "compression": {
                    "bitrate": "2500k",
                    "preset": "medium",
                    "max_size": 1_000_000_000  # 1GB
                }
            }
        }
        
        self.quality_presets = {
            "HD": {
                "height": 720,
                "bitrate": "2000k",
                "audio_bitrate": "128k"
            },
            "FHD": {
                "height": 1080,
                "bitrate": "4000k",
                "audio_bitrate": "192k"
            },
            "4K": {
                "height": 2160,
                "bitrate": "8000k",
                "audio_bitrate": "320k"
            }
        }

    def detect_content_type(self, filename: str, duration: Optional[int] = None) -> str:
        """Detecta el tipo de contenido basado en el nombre del archivo y duración"""
        filename = filename.lower()
        
        # Detectar por patrones en el nombre
        for content_type, config in self.content_types.items():
            for pattern in config["patterns"]:
                if re.search(pattern, filename):
                    return content_type
        
        # Si no se detectó por nombre, usar duración como fallback
        if duration:
            if duration < 3600:  # menos de 1 hora
                return "series"
            else:
                return "movies"
        
        return "default"

    async def get_processing_config(self, file_info: Dict) -> Dict:
        """Genera la configuración de procesamiento basada en el tipo de contenido"""
        filename = file_info.get("file_name", "")
        media_info = file_info.get("media_info", {})
        duration = media_info.get("duration", 0)
        
        content_type = self.detect_content_type(filename, duration)
        type_config = self.content_types.get(content_type, {})
        
        # Obtener resolución original
        width = media_info.get("width", 1920)
        height = media_info.get("height", 1080)
        
        # Determinar preset de calidad
        target_preset = "HD"
        if height >= 2160:
            target_preset = "4K"
        elif height >= 1080:
            target_preset = "FHD"
        
        quality_config = self.quality_presets[target_preset]
        
        # Configuración final
        config = {
            "watermark": {
                "file": type_config.get("watermark"),
                "position": "bottomright",
                "size": 0.1  # 10% del ancho del video
            },
            "thumbnail": {
                "overlay": type_config.get("thumbnail_overlay"),
                "position": "center"
            },
            "compression": {
                **type_config.get("compression", {}),
                **quality_config
            },
            "content_type": content_type,
            "auto_split": duration > 7200,  # dividir si dura más de 2 horas
            "metadata": {
                "title": self._clean_filename(filename),
                "content_type": content_type,
                "processed_date": datetime.utcnow().isoformat()
            }
        }
        
        return config

    async def process_thumbnail(self, video_path: str, config: Dict) -> Optional[str]:
        """Procesa y genera un thumbnail personalizado"""
        try:
            # Extraer frame del medio del video
            media_info = get_media_info(video_path)
            duration = float(media_info.get("format", {}).get("duration", "0"))
            thumbnail_time = duration / 2
            
            # TODO: Implementar extracción de thumbnail y overlay
            return None
            
        except Exception as e:
            logger.error(f"Error procesando thumbnail: {e}")
            return None

    async def should_auto_process(self, file_info: Dict) -> bool:
        """Determina si un archivo debe ser procesado automáticamente"""
        filename = file_info.get("file_name", "").lower()
        
        # No procesar si ya está procesado
        if "processed_" in filename:
            return False
            
        # No procesar archivos muy pequeños
        if file_info.get("file_size", 0) < 1_000_000:  # 1MB
            return False
            
        # Verificar extensión
        valid_extensions = [".mp4", ".mkv", ".avi", ".mov", ".wmv"]
        if not any(filename.endswith(ext) for ext in valid_extensions):
            return False
            
        return True

    def _clean_filename(self, filename: str) -> str:
        """Limpia y formatea el nombre del archivo"""
        # Remover extensión
        name = os.path.splitext(filename)[0]
        
        # Remover caracteres especiales
        name = re.sub(r'[^\w\s-]', '', name)
        
        # Remover calidades de video
        name = re.sub(r'(?i)(1080p|720p|2160p|HDTV|BluRay|WEB-DL)', '', name)
        
        # Limpiar espacios
        name = re.sub(r'\s+', ' ', name).strip()
        
        return name

    async def auto_rename_file(self, file_info: Dict) -> str:
        """Genera un nombre de archivo automático basado en el contenido"""
        original_name = file_info.get("file_name", "")
        content_type = self.detect_content_type(original_name)
        clean_name = self._clean_filename(original_name)
        
        # Agregar prefijo según tipo
        prefix = {
            "series": "Serie",
            "movies": "Pelicula",
            "shows": "Show"
        }.get(content_type, "Video")
        
        # Formatear nombre final
        processed_name = f"{prefix} - {clean_name}"
        
        # Añadir calidad si se detecta
        if re.search(r'(?i)(1080p|720p|2160p)', original_name):
            quality = re.search(r'(?i)(1080p|720p|2160p)', original_name).group(1)
            processed_name += f" [{quality}]"
            
        return processed_name