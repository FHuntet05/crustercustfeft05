from typing import Dict, Any
from .utils import format_bytes, format_time, escape_html

class BotMessages:
    @staticmethod
    def task_status(task: Dict[str, Any]) -> str:
        """Genera un mensaje de estado de tarea rico en formato"""
        file_name = task.get('file_name', 'Sin nombre')
        status = task.get('status', 'desconocido')
        metadata = task.get('metadata', {})
        
        status_emoji = {
            'pending_processing': '⏳',
            'queued': '🔄',
            'processing': '⚙️',
            'completed': '✅',
            'error': '❌'
        }.get(status, '❓')
        
        msg = [f"{status_emoji} <b>Estado:</b> {status.upper()}"]
        msg.append(f"📁 <b>Archivo:</b> {escape_html(file_name)}")
        
        if size := metadata.get('size'):
            msg.append(f"📊 <b>Tamaño:</b> {format_bytes(size)}")
        
        if duration := metadata.get('duration'):
            msg.append(f"⏱ <b>Duración:</b> {format_time(duration)}")
            
        if resolution := metadata.get('resolution'):
            msg.append(f"🎥 <b>Resolución:</b> {resolution}")
            
        if error := task.get('error_details'):
            msg.append(f"⚠️ <b>Error:</b> {escape_html(error)}")
            
        return "\n".join(msg)

    @staticmethod
    def processing_status(current: int, total: int, action: str, speed: float, eta: float, elapsed: float) -> str:
        """Genera un mensaje de progreso detallado"""
        percent = current * 100 / total if total > 0 else 0
        blocks = int(percent / 7.7)  # 13 bloques en total
        
        msg = [
            f"⚡️ <b>{action}...</b>",
            f"[{'▤' * blocks}{'□' * (13 - blocks)}] {percent:.1f}%",
            f"┠ Procesado: {format_bytes(current)} de {format_bytes(total)}",
            f"┠ Velocidad: {format_bytes(speed)}/s",
            f"┠ ETA: {format_time(eta)}",
            f"┠ Transcurrido: {format_time(elapsed)}"
        ]
        
        return "\n".join(msg)

    @staticmethod
    def download_started(info: Dict[str, Any]) -> str:
        """Mensaje al iniciar una descarga"""
        msg = [
            "📥 <b>Iniciando Descarga</b>\n",
            f"📁 <b>Archivo:</b> {escape_html(info.get('file_name', 'Sin nombre'))}",
            f"📊 <b>Tamaño:</b> {format_bytes(info.get('file_size', 0))}"
        ]
        
        if duration := info.get('duration'):
            msg.append(f"⏱ <b>Duración:</b> {format_time(duration)}")
            
        if 'width' in info and 'height' in info:
            msg.append(f"🎥 <b>Resolución:</b> {info['width']}x{info['height']}")
        
        msg.extend([
            "",
            "ℹ️ Puede usar /cancel en cualquier momento para detener el proceso."
        ])
        
        return "\n".join(msg)

    @staticmethod
    def process_complete(task: Dict[str, Any], stats: Dict[str, Any]) -> str:
        """Mensaje de proceso completado con estadísticas"""
        msg = [
            "✅ <b>¡Proceso Completado!</b>\n",
            f"📁 <b>Archivo:</b> {escape_html(task.get('file_name', 'Sin nombre'))}",
            f"📊 <b>Tamaño Final:</b> {format_bytes(stats.get('final_size', 0))}",
            f"⚡️ <b>Velocidad Promedio:</b> {format_bytes(stats.get('avg_speed', 0))}/s",
            f"⏱ <b>Tiempo Total:</b> {format_time(stats.get('total_time', 0))}",
            "",
            "🎯 <b>Resultados:</b>",
            f"• Compresión: {stats.get('compression_ratio', 0):.1f}%",
            f"• Ahorro: {format_bytes(stats.get('saved_size', 0))}"
        ]
        
        return "\n".join(msg)