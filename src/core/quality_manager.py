import logging
from typing import Dict, Any

from src.db.mongo_manager import db_instance
from src.core.exceptions import ProcessingError

logger = logging.getLogger(__name__)

class QualityPresets:
    """Presets de calidad para diferentes tipos de contenido"""
    
    MOVIE_PRESETS = {
        '4K': {
            'height': 2160,
            'crf': '18',
            'preset': 'slow',
            'max_rate': '20000k',
            'buf_size': '40000k',
            'audio_bitrate': '384k'
        },
        '1080p': {
            'height': 1080,
            'crf': '20',
            'preset': 'medium',
            'max_rate': '5000k',
            'buf_size': '10000k',
            'audio_bitrate': '192k'
        },
        '720p': {
            'height': 720,
            'crf': '23',
            'preset': 'medium',
            'max_rate': '2500k',
            'buf_size': '5000k',
            'audio_bitrate': '128k'
        },
        '480p': {
            'height': 480,
            'crf': '26',
            'preset': 'medium',
            'max_rate': '1000k',
            'buf_size': '2000k',
            'audio_bitrate': '96k'
        }
    }
    
    SERIES_PRESETS = {
        '1080p': {
            'height': 1080,
            'crf': '23',
            'preset': 'medium',
            'max_rate': '4000k',
            'buf_size': '8000k',
            'audio_bitrate': '192k'
        },
        '720p': {
            'height': 720,
            'crf': '26',
            'preset': 'medium',
            'max_rate': '2000k',
            'buf_size': '4000k',
            'audio_bitrate': '128k'
        },
        '480p': {
            'height': 480,
            'crf': '28',
            'preset': 'medium',
            'max_rate': '800k',
            'buf_size': '1600k',
            'audio_bitrate': '96k'
        }
    }
    
    DEFAULT_PRESETS = {
        '1080p': {
            'height': 1080,
            'crf': '26',
            'preset': 'medium',
            'max_rate': '3000k',
            'buf_size': '6000k',
            'audio_bitrate': '192k'
        },
        '720p': {
            'height': 720,
            'crf': '28',
            'preset': 'medium',
            'max_rate': '1500k',
            'buf_size': '3000k',
            'audio_bitrate': '128k'
        },
        '480p': {
            'height': 480,
            'crf': '30',
            'preset': 'medium',
            'max_rate': '600k',
            'buf_size': '1200k',
            'audio_bitrate': '96k'
        }
    }

class QualityManager:
    """Gestiona la calidad y compresión de los archivos"""
    
    def __init__(self):
        self.presets = {
            'movies': QualityPresets.MOVIE_PRESETS,
            'series': QualityPresets.SERIES_PRESETS,
            'default': QualityPresets.DEFAULT_PRESETS
        }
        
    def get_optimal_quality(self, file_info: Dict[str, Any], content_type: str = 'default') -> Dict[str, Any]:
        """
        Determina la calidad óptima basada en el archivo de entrada
        """
        try:
            # Extraer información relevante
            height = file_info.get('height', 0)
            size = file_info.get('size', 0)
            duration = file_info.get('duration', 0)
            
            if not all([height, size, duration]):
                raise ProcessingError("Información insuficiente para determinar la calidad óptima")
            
            # Calcular MB por minuto
            mb_per_minute = (size / 1024 / 1024) / (duration / 60)
            logger.info(f"Tamaño por minuto: {mb_per_minute:.2f} MB/min")
            
            # Seleccionar preset base según tipo de contenido
            presets = self.presets.get(content_type, self.presets['default'])
            
            # Determinar calidad objetivo
            target_quality = self._get_target_quality(height, mb_per_minute, content_type)
            preset = presets.get(target_quality, presets['720p'])  # 720p como fallback
            
            # Ajustar parámetros según el tamaño por minuto
            adjusted_preset = self._adjust_preset_for_size(preset.copy(), mb_per_minute)
            
            logger.info(f"Calidad seleccionada: {target_quality} con ajustes personalizados")
            return adjusted_preset
            
        except Exception as e:
            logger.error(f"Error determinando calidad: {e}")
            # Retornar preset seguro por defecto
            return self.presets['default']['720p']
            
    def _get_target_quality(self, height: int, mb_per_minute: float, content_type: str) -> str:
        """Determina la calidad objetivo basada en la resolución y tamaño"""
        
        if content_type == 'movies':
            if height >= 2160 and mb_per_minute < 100:
                return '4K'  # Mantener 4K solo si el tamaño es razonable
            elif height >= 1080:
                return '1080p' if mb_per_minute < 50 else '720p'
            elif height >= 720:
                return '720p' if mb_per_minute < 30 else '480p'
            return '480p'
            
        elif content_type == 'series':
            if height >= 1080:
                return '1080p' if mb_per_minute < 40 else '720p'
            elif height >= 720:
                return '720p' if mb_per_minute < 25 else '480p'
            return '480p'
            
        else:  # default
            if height >= 1080:
                return '1080p' if mb_per_minute < 30 else '720p'
            elif height >= 720:
                return '720p' if mb_per_minute < 20 else '480p'
            return '480p'
            
    def _adjust_preset_for_size(self, preset: Dict[str, Any], mb_per_minute: float) -> Dict[str, Any]:
        """Ajusta los parámetros del preset según el tamaño por minuto"""
        
        # Ajustar CRF basado en el tamaño
        if mb_per_minute > 50:
            preset['crf'] = str(int(preset['crf']) + 2)  # Más compresión
        elif mb_per_minute < 10:
            preset['crf'] = str(max(int(preset['crf']) - 2, 17))  # Menos compresión, mínimo 17
            
        # Ajustar preset de velocidad
        if mb_per_minute > 100:
            preset['preset'] = 'faster'  # Priorizar velocidad para archivos muy grandes
        elif mb_per_minute < 20:
            preset['preset'] = 'slow'  # Priorizar calidad para archivos pequeños
            
        return preset
        
quality_manager = QualityManager()