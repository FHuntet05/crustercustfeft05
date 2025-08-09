import asyncio
import logging
import subprocess
import shlex
import os
import re
import json
from typing import List, Tuple

from src.core.exceptions import FFmpegProcessingError

logger = logging.getLogger(__name__)

# --- Funciones Públicas ---

def get_media_info(file_path: str) -> dict:
    """
    Obtiene metadatos de un archivo de medios usando ffprobe.
    """
    if not os.path.exists(file_path):
        return {}
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", shlex.quote(file_path)]
    try:
        # Usamos shlex.join para mayor seguridad al construir el comando para el log
        logger.debug(f"Ejecutando ffprobe: {shlex.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_command_for_task(task: dict, input_paths: List[str], output_path: str, watermark_path: str = None) -> Tuple[List[str], str]:
    """
    Función despachadora principal.
    Analiza la tarea y delega la construcción del comando a la función apropiada.
    """
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    # --- Despacho a constructores especializados ---
    if file_type == 'join_operation':
        return _build_join_command(input_paths, output_path)
    if file_type == 'zip_operation':
        return _build_zip_command(input_paths, output_path)
        
    # Para todas las demás operaciones, esperamos una única ruta de entrada.
    input_path = input_paths[0] if input_paths else None
    if not input_path:
        raise ValueError("Se requiere una ruta de entrada para esta operación.")

    if config.get('extract_audio'):
        return _build_extract_audio_command(input_path, output_path)
    if 'gif_options' in config:
        return _build_gif_command(config, input_path, output_path)
    
    # Si no es un caso especial, usamos el constructor estándar.
    return _build_standard_ffmpeg_command(task, input_path, output_path, watermark_path)

# --- Constructores de Comandos Privados ---

def _build_standard_ffmpeg_command(task: dict, input_path: str, output_path: str, watermark_path: str = None) -> Tuple[List[str], str]:
    """
    Construye el comando FFmpeg para operaciones estándar de video y audio.
    """
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    # --- Garantizar Extensión de Salida ---
    base_output_path, _ = os.path.splitext(output_path)
    if file_type == 'video':
        # Los GIFs se manejan en su propia función, así que aquí siempre es mp4.
        output_path = f"{base_output_path}.mp4"
    elif file_type == 'audio':
        output_path = f"{base_output_path}.{config.get('audio_format', 'mp3')}"
    
    command_parts = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    
    # --- Manejo de Entradas (Inputs) ---
    if trim_times := config.get('trim_times'):
        start, _, end = trim_times.partition('-')
        if start.strip(): command_parts.extend(["-ss", start.strip()])
    
    command_parts.extend(["-i", shlex.quote(input_path)])
    
    if trim_times and end.strip():
        # -to debe ir después de -i para que funcione correctamente con -ss
        command_parts.extend(["-to", end.strip()])

    filter_complex_parts = []
    video_filters, audio_filters = [], []
    
    video_chain_input, audio_chain_input = "[0:v]", "[0:a]"
    final_video_output_map, final_audio_output_map = "-map", "[outv]"
    
    # --- Lógica de Filter Graph (Video) ---
    if file_type == 'video':
        if transcode_config := config.get('transcode'):
            if res := transcode_config.get('resolution'):
                video_filters.append(f"scale=-2:{res.replace('p', '')}")
        
        if watermark_path and config.get('watermark', {}).get('type') == 'image':
            command_parts.extend(["-i", shlex.quote(watermark_path)])
            pos_map = {'top_left': '10:10', 'top_right': 'W-w-10:10', 'bottom_left': '10:H-h-10', 'bottom_right': 'W-w-10:H-h-10'}
            overlay_position = pos_map.get(config['watermark'].get('position', 'top_right'), 'W-w-10:10')
            filter_complex_parts.append(f"[0:v][1:v]overlay={overlay_position}[v_watermarked]")
            video_chain_input = "[v_watermarked]" # La siguiente cadena de filtros operará sobre la salida con marca de agua.
        
        elif watermark_config := config.get('watermark'):
            if watermark_config.get('type') == 'text':
                # Escapar caracteres especiales para el filtro drawtext
                text = watermark_config.get('text', '').replace("'", "’").replace(':', r'\:').replace('%', r'\%').replace('"', '”')
                pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
                drawtext_position = pos_map.get(watermark_config.get('position', 'top_right'), 'x=w-text_w-10:y=h-text_h-10')
                video_filters.append(f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{drawtext_position}")
    
    # --- Lógica de Filter Graph (Audio) ---
    if config.get('slowed'): audio_filters.append("atempo=0.8")
    if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")

    # --- Construcción y Aplicación del Filter Graph ---
    if video_filters:
        filter_complex_parts.append(f"{video_chain_input}{','.join(video_filters)}[v_filtered]")
        final_video_output_map, final_audio_output_map = "-map", "[v_filtered]"
    if audio_filters:
        filter_complex_parts.append(f"{audio_chain_input}{','.join(audio_filters)}[a_filtered]")
        final_audio_output_map = "[a_filtered]"

    if filter_complex_parts:
        # [CRITICAL FIX] Envolver toda la cadena de filter_complex en comillas
        # para que el shell la trate como un solo argumento.
        filter_complex_str = f"\"{';'.join(filter_complex_parts)}\""
        command_parts.extend(['-filter_complex', filter_complex_str])

    # --- Mapeo y Codificación ---
    if file_type == 'video':
        command_parts.extend(["-map", final_video_output_map])
        if not config.get('mute_audio'):
             command_parts.extend(["-map", final_audio_output_map])
        
        # Codecs
        if config.get('transcode'):
            command_parts.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"])
        else:
            command_parts.extend(["-c:v", "copy"])
        
        if not config.get('mute_audio'):
            command_parts.extend(["-c:a", "aac", "-b:a", "128k"])
        else:
            command_parts.append("-an") # No audio

    elif file_type == 'audio':
        fmt, bitrate = config.get('audio_format', 'mp3'), config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        command_parts.extend(["-map", final_audio_output_map, "-c:a", codec_map.get(fmt, 'libmp3lame')])
        if fmt != 'flac': command_parts.extend(["-b:a", bitrate])
        command_parts.append("-vn") # No video

    command_parts.append(shlex.quote(output_path))
    
    # Usar shlex.join para una construcción segura del comando final para logging
    final_command_str = shlex.join(command_parts)
    logger.info(f"Comando FFmpeg construido: {final_command_str}")
    return [final_command_str], output_path

def _build_extract_audio_command(input_path: str, output_path_base: str) -> Tuple[List[str], str]:
    media_info = get_media_info(input_path)
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}.get(codec_name, '.m4a')
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = f"ffmpeg -y -i {shlex.quote(input_path)} -vn -c:a copy {shlex.quote(final_output_path)}"
    return [command], final_output_path

def _build_gif_command(config: dict, input_path: str, output_path: str) -> Tuple[List[str], str]:
    gif_opts = config['gif_options']
    duration, fps = gif_opts.get('duration', 5), gif_opts.get('fps', 15)
    output_path = f"{os.path.splitext(output_path)[0]}.gif"
    palette_path = f"{os.path.splitext(output_path)[0]}.palette.png"
    filters = f"fps={fps},scale=480:-1:flags=lanczos"
    
    cmd1 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -vf \"{filters},palettegen\" -y {shlex.quote(palette_path)}"
    cmd2 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} -lavfi \"{filters} [x]; [x][1:v] paletteuse\" -y {shlex.quote(output_path)}"
    
    # Devuelve una lista con dos comandos a ejecutar en secuencia.
    return [cmd1, cmd2], output_path

def _build_join_command(input_files: List[str], output_path: str) -> Tuple[List[str], str]:
    """
    Construye el comando para unir varios videos usando el método concat de FFmpeg.
    """
    base_output_path, _ = os.path.splitext(output_path)
    final_output_path = f"{base_output_path}.mp4"
    
    # Crear un archivo de lista temporal
    list_file_path = f"{base_output_path}_concat_list.txt"
    with open(list_file_path, 'w', encoding='utf-8') as f:
        for file_path in input_files:
            # 'file' keyword es requerido por el demuxer concat.
            # shlex.quote es crucial si las rutas tienen espacios o caracteres especiales.
            f.write(f"file {shlex.quote(file_path)}\n")
            
    command = f"ffmpeg -y -f concat -safe 0 -i {shlex.quote(list_file_path)} -c copy {shlex.quote(final_output_path)}"
    logger.info(f"Comando de unión construido. Usará el archivo de lista: {list_file_path}")
    
    # Devolvemos el comando y la ruta del archivo final. El worker se encargará de limpiar el .txt
    return [command], final_output_path

def _build_zip_command(input_files: List[str], output_path: str) -> Tuple[List[str], str]:
    """
    Construye el comando para comprimir varios archivos en un .zip.
    """
    base_output_path, _ = os.path.splitext(output_path)
    final_output_path = f"{base_output_path}.zip"
    
    # Usamos shlex.quote para cada archivo y para la salida.
    quoted_files = [shlex.quote(f) for f in input_files]
    
    # -j "junks" (aplana) la estructura de directorios.
    command = f"zip -j {shlex.quote(final_output_path)} {' '.join(quoted_files)}"
    logger.info("Comando de compresión ZIP construido.")
    
    return [command], final_output_path