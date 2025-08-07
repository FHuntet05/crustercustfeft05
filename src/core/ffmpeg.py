# src/core/ffmpeg.py

import asyncio
import logging
import os

# Configuración del logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_ffmpeg_command(command):
    """Ejecuta un comando de FFmpeg de forma asíncrona."""
    logger.info(f"Ejecutando comando FFmpeg: {' '.join(command)}")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        error_message = stderr.decode('utf-8', errors='ignore').strip()
        logger.error(f"Error en FFmpeg: {error_message}")
        raise RuntimeError(f"FFmpeg falló: {error_message}")
    
    logger.info("Comando FFmpeg ejecutado con éxito.")
    return stdout.decode('utf-8', errors='ignore').strip()

def get_safe_output_path(input_path, suffix, new_ext=None):
    """
    Genera un nombre de archivo de salida seguro, evitando conflictos.
    """
    directory, filename = os.path.split(input_path)
    name, original_ext = os.path.splitext(filename)
    
    # Determinar la extensión final
    if new_ext:
        final_ext = f".{new_ext.lstrip('.')}" # Asegura que solo haya un punto
    else:
        final_ext = original_ext or ".mkv" # Fallback a .mkv si no hay extensión
        
    return os.path.join(directory, f"{name}_{suffix}{final_ext}")

async def embed_thumbnail(media_path: str, thumbnail_path: str, output_path: str):
    """
    Incrusta una imagen como carátula en un archivo de audio/video usando FFmpeg.
    Copia los códecs para máxima velocidad y calidad.
    """
    if not os.path.exists(media_path):
        raise FileNotFoundError(f"El archivo de medios no existe: {media_path}")
    if not os.path.exists(thumbnail_path):
        # Si la carátula no existe, simplemente no hacemos nada y evitamos un error.
        logger.warning(f"El archivo de carátula no existe: {thumbnail_path}. Se omitirá la incrustación.")
        return None # Indica que no se generó un nuevo archivo

    # Comando para incrustar la carátula. Usamos -map 0 y -map 1 para ser explícitos.
    # -c copy copia el stream de audio sin recodificar.
    # -disposition:v:0 attached_pic establece la imagen como la carátula.
    command = [
        'ffmpeg', '-i', media_path, '-i', thumbnail_path,
        '-map', '0', '-map', '1',      # Mapea todos los streams del input 0 y 1
        '-c', 'copy',                 # Copia los streams sin recodificar
        '-c:v:0', 'mjpeg',            # Codifica la imagen a un formato compatible
        '-disposition:v:1', 'attached_pic', # Marca la imagen como carátula
        '-id3v2_version', '3',        # Estándar común para metadatos
        '-metadata:s:v', 'title="Album cover"',
        '-metadata:s:v', 'comment="Cover (front)"',
        '-y',                         # Sobrescribe el archivo de salida si existe
        output_path
    ]
    
    await run_ffmpeg_command(command)
    logger.info(f"Carátula incrustada en {output_path}")
    return output_path

# Aquí irán las futuras funciones de ffmpeg (trim, split, gif, etc.)
# async def trim_media(...):
#     pass