import asyncio
import logging
import subprocess
import shlex
import os
import re
import json

from src.core.exceptions import FFmpegProcessingError

logger = logging.getLogger(__name__)

def get_media_info(file_path: str) -> dict:
    if not os.path.exists(file_path): return {}
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", file_path]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}"); return {}

def build_ffmpeg_command(task: dict, input_path: str, output_path: str, watermark_path: str = None, subs_path: str = None, new_audio_path: str = None) -> tuple[list[str], str]:
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    # --- Garantizar Extensión de Salida ---
    base_output_path, _ = os.path.splitext(output_path)
    if file_type == 'video':
        if 'gif_options' in config: output_path = f"{base_output_path}.gif"
        else: output_path = f"{base_output_path}.mp4"
    elif file_type == 'audio':
        output_path = f"{base_output_path}.{config.get('audio_format', 'mp3')}"
    
    # --- Delegación a constructores especializados ---
    if config.get('extract_audio'): return build_extract_audio_command(input_path, output_path)
    if 'gif_options' in config: return build_gif_command(config, input_path, output_path)
    
    # --- Constructor de Comando Principal ---
    command_parts = ["ffmpeg", "-y"]
    if trim_times := config.get('trim_times'):
        start, _, end = trim_times.partition('-')
        if start.strip(): command_parts.extend(["-ss", start.strip()])
        if end.strip(): command_parts.extend(["-to", end.strip()])
    
    command_parts.extend(["-i", shlex.quote(input_path)])
    input_map_idx = 0
    if watermark_path:
        command_parts.extend(["-i", shlex.quote(watermark_path)]); watermark_map_idx = input_map_idx + 1

    # --- Lógica de Filter Graph Unificada ---
    filter_complex_parts = []
    video_filters, audio_filters = [], []
    video_chain_input, audio_chain_input = f"[{input_map_idx}:v]", f"[{input_map_idx}:a]"
    video_chain_output, audio_chain_output = "[outv]", "[outa]"

    if file_type == 'video':
        if transcode_config := config.get('transcode'):
            if res := transcode_config.get('resolution'): video_filters.append(f"scale=-2:{res.replace('p', '')}")
        
        if watermark_config := config.get('watermark'):
            if watermark_config.get('type') == 'text':
                text = watermark_config.get('text', '').replace("'", "’").replace(':', r'\:')
                pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
                video_filters.append(f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{pos_map.get(watermark_config.get('position', 'top_right'))}")
    
    if config.get('slowed'): audio_filters.append("atempo=0.8")
    if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")

    # Construir las cadenas de filtros
    final_video_chain = f"{video_chain_input}{','.join(video_filters)}[v_filtered]" if video_filters else f"{video_chain_input}null[v_filtered]"
    final_audio_chain = f"{audio_chain_input}{','.join(audio_filters)}[a_filtered]" if audio_filters else f"{audio_chain_input}anull[a_filtered]"
    
    filter_complex_parts.extend([final_video_chain, final_audio_chain])
    
    # Manejar overlay de marca de agua de imagen
    if watermark_path and config.get('watermark', {}).get('type') == 'image':
        pos_map = {'top_left': '10:10', 'top_right': 'W-w-10:10', 'bottom_left': '10:H-h-10', 'bottom_right': 'W-w-10:H-h-10'}
        overlay_filter = f"[v_filtered][{watermark_map_idx}:v]overlay={pos_map.get(config['watermark'].get('position', 'top_right'))}{video_chain_output}"
        filter_complex_parts.append(overlay_filter)
        # La pista de audio pasa directamente a la salida
        filter_complex_parts.append(f"[a_filtered]anull{audio_chain_output}")
    else:
        # Si no hay overlay, las pistas filtradas van directamente a la salida
        filter_complex_parts.append(f"[v_filtered]null{video_chain_output}")
        filter_complex_parts.append(f"[a_filtered]anull{audio_chain_output}")

    command_parts.extend(['-filter_complex', ";".join(filter_complex_parts)])

    # --- Mapeo y Codificación ---
    if file_type == 'video':
        command_parts.extend(["-map", video_chain_output, "-map", audio_chain_output])
        if config.get('transcode'):
            command_parts.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "128k"])
        else:
            command_parts.extend(["-c:v", "copy", "-c:a", "copy"])
    elif file_type == 'audio':
        fmt, bitrate = config.get('audio_format', 'mp3'), config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        command_parts.extend(["-map", audio_chain_output, "-c:a", codec_map.get(fmt, 'libmp3lame')])
        if fmt != 'flac': command_parts.extend(["-b:a", bitrate])
        command_parts.append("-vn")

    if config.get('mute_audio'):
        command_parts = [p for p in command_parts if not (p.startswith(("-c:a", "-b:a")) or p == audio_chain_output)]
        command_parts.append("-an")

    command_parts.append(shlex.quote(output_path))
    final_command = " ".join(command_parts)
    logger.info(f"Comando FFmpeg construido: {final_command}")
    return [final_command], output_path

def build_extract_audio_command(input_path: str, output_path_base: str) -> tuple[list[str], str]:
    media_info = get_media_info(input_path)
    audio_stream = next((s for s in media_info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    ext = ".m4a"
    if audio_stream and (codec_name := audio_stream.get('codec_name')):
        ext = {'mp3': '.mp3', 'aac': '.m4a', 'opus': '.opus', 'flac': '.flac'}.get(codec_name, '.m4a')
    final_output_path = f"{os.path.splitext(output_path_base)[0]}{ext}"
    command = f"ffmpeg -y -i {shlex.quote(input_path)} -vn -c:a copy {shlex.quote(final_output_path)}"
    return [command], final_output_path

def build_gif_command(config, input_path, output_path) -> tuple[list[str], str]:
    gif_opts = config['gif_options']; duration, fps = gif_opts['duration'], gif_opts['fps']
    palette_path = f"{os.path.splitext(output_path)[0]}.palette.png"; filters = f"fps={fps},scale=480:-1:flags=lanczos"
    cmd1 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -vf \"{filters},palettegen\" -y {shlex.quote(palette_path)}"
    cmd2 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} -lavfi \"{filters} [x]; [x][1:v] paletteuse\" {shlex.quote(output_path)}"
    return [cmd1, cmd2], output_path