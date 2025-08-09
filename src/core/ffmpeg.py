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
    if not os.path.exists(file_path):
        logger.error(f"ffprobe no puede encontrar el archivo: {file_path}")
        return {}
    command = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", file_path]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"No se pudo obtener info de {file_path}: {e}")
        return {}

def build_multi_trim_command(trim_times_str: str, input_path: str, output_path: str) -> tuple[list[str], str]:
    segments = [s.strip() for s in trim_times_str.split(',') if s.strip()]
    filter_chains, concat_inputs = [], []
    for i, segment in enumerate(segments):
        start, _, end = segment.partition('-')
        if not start or not end: continue
        filter_chains.append(f"[0:v]trim=start={start.strip()}:end={end.strip()},setpts=PTS-STARTPTS[v{i}]")
        filter_chains.append(f"[0:a]atrim=start={start.strip()}:end={end.strip()},asetpts=PTS-STARTPTS[a{i}]")
        concat_inputs.append(f"[v{i}][a{i}]")

    if not filter_chains: raise ValueError("Formato de multi-corte inválido.")
    final_filter_complex = ";".join(filter_chains + [f"{''.join(concat_inputs)}concat=n={len(segments)}:v=1:a=1[outv][outa]"])
    command_parts = ["ffmpeg", "-y", "-i", shlex.quote(input_path), "-filter_complex", final_filter_complex, "-map", "[outv]", "-map", "[outa]", shlex.quote(output_path)]
    return [" ".join(command_parts)], output_path

def build_ffmpeg_command(task: dict, input_path: str, output_path: str, watermark_path: str = None, subs_path: str = None, new_audio_path: str = None) -> tuple[list[str], str]:
    config = task.get('processing_config', {})
    file_type = task.get('file_type')
    
    # --- GARANTIZAR LA EXTENSIÓN DEL ARCHIVO DE SALIDA ---
    base_output_path, _ = os.path.splitext(output_path)
    if file_type == 'video':
        # Para GIF, la extensión es manejada por su propia función
        if 'gif_options' not in config:
            output_path = f"{base_output_path}.mp4"
    elif file_type == 'audio':
        ext = config.get('audio_format', 'mp3')
        output_path = f"{base_output_path}.{ext}"
    
    # --- Delegación a constructores especializados ---
    if config.get('extract_audio'): return build_extract_audio_command(input_path, output_path)
    if 'gif_options' in config: return build_gif_command(config, input_path, output_path)
    if 'split_criteria' in config: return build_split_command(config, input_path, output_path)
    if trim_times := config.get('trim_times'):
        if ',' in trim_times: return build_multi_trim_command(trim_times, input_path, output_path)

    # --- Constructor de Comando Principal ---
    command_parts = ["ffmpeg", "-y"]
    if trim_times:
        start, _, end = trim_times.partition('-'); command_parts.extend(["-ss", start.strip()] if start.strip() else []); command_parts.extend(["-to", end.strip()] if end.strip() else [])
    
    command_parts.extend(["-i", shlex.quote(input_path)])
    
    filter_complex_parts, video_filters, audio_filters = [], [], []
    video_chain, audio_chain = "[0:v]", "[0:a]"

    if file_type == 'video':
        if transcode_config := config.get('transcode'):
            if resolution := transcode_config.get('resolution'): video_filters.append(f"scale=-2:{resolution.replace('p', '')}")
        if watermark_config := config.get('watermark'):
            if watermark_config.get('type') == 'text':
                text = watermark_config.get('text', '').replace("'", "’").replace(':', r'\:')
                pos_map = {'top_left': 'x=10:y=10', 'top_right': 'x=w-text_w-10:y=10', 'bottom_left': 'x=10:y=h-text_h-10', 'bottom_right': 'x=w-text_w-10:y=h-text_h-10'}
                video_filters.append(f"drawtext=text='{text}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{pos_map.get(watermark_config.get('position', 'top_right'))}")
    
    if config.get('slowed'): audio_filters.append("atempo=0.8")
    if config.get('reverb'): audio_filters.append("aecho=0.8:0.9:1000:0.3")

    if video_filters: video_chain = f"{video_chain}{','.join(video_filters)}[filtered_v]"; filter_complex_parts.append(video_chain); video_chain = "[filtered_v]"
    if audio_filters: audio_chain = f"{audio_chain}{','.join(audio_filters)}[filtered_a]"; filter_complex_parts.append(audio_chain); audio_chain = "[filtered_a]"
    
    if filter_complex_parts: command_parts.extend(['-filter_complex', ";".join(filter_complex_parts)])

    # --- Mapeo y Codificación ---
    if file_type == 'video':
        command_parts.extend(["-map", video_chain if video_chain.startswith("[") else "0:v:0?"])
        command_parts.extend(["-map", audio_chain if audio_chain.startswith("[") else "0:a:0?"])
        if config.get('transcode'): command_parts.extend(["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "128k"])
        else: command_parts.extend(["-c:v", "copy"]); command_parts.extend(["-c:a", "aac"] if audio_chain.startswith("[") else ["-c:a", "copy"])
    elif file_type == 'audio':
        fmt, bitrate = config.get('audio_format', 'mp3'), config.get('audio_bitrate', '192k')
        codec_map = {'mp3': 'libmp3lame', 'flac': 'flac', 'opus': 'libopus'}
        command_parts.extend(["-map", "0:a", "-c:a", codec_map.get(fmt, 'libmp3lame')])
        if fmt != 'flac': command_parts.extend(["-b:a", bitrate])
        command_parts.append("-vn") # Asegurar que no se copie ninguna pista de video/imagen
        if 'audio_tags' in config:
            tags = config['audio_tags']
            for key, value in tags.items(): command_parts.extend(["-metadata", f"{key}={shlex.quote(str(value))}"])

    if config.get('mute_audio'):
        command_parts = [p for p in command_parts if not (p.startswith(("-c:a", "-b:a")) or (p.startswith("-map") and "a" in p))]
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
    cmd1 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -vf \"{filters},palettegen\" {shlex.quote(palette_path)}"
    final_gif_path = f"{os.path.splitext(output_path)[0]}.gif"
    cmd2 = f"ffmpeg -y -ss 0 -t {duration} -i {shlex.quote(input_path)} -i {shlex.quote(palette_path)} -lavfi \"{filters} [x]; [x][1:v] paletteuse\" {shlex.quote(final_gif_path)}"
    return [cmd1, cmd2], final_gif_path

def build_split_command(config, input_path, output_path) -> tuple[list[str], str]:
    criteria = config['split_criteria']; base_name, ext = os.path.splitext(output_path)
    final_output_pattern = f"{base_name}_part%03d{ext or '.mp4'}"
    command = (f"ffmpeg -y -i {shlex.quote(input_path)} -c copy -map 0 "
               f"-segment_time {criteria.lower().replace('s', '')} -f segment -reset_timestamps 1 {shlex.quote(final_output_pattern)}")
    return [command], f"{base_name}_part*{ext or '.mp4'}"