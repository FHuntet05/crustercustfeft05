from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def build_panel_keyboard(tasks):
    """Construye el teclado para la mesa de trabajo (/panel)."""
    keyboard = []
    
    # Crear botones para cada tarea
    for task in tasks:
        # El ID de MongoDB es un ObjectId, lo convertimos a string para usarlo en el callback_data
        task_id = str(task.get('_id'))
        
        # Acortamos el nombre del archivo para que quepa bien en el botón
        file_name = task.get('file_name', 'Archivo sin nombre')
        short_name = (file_name[:25] + '...') if len(file_name) > 28 else file_name

        keyboard.append([
            InlineKeyboardButton(f"🎬 Procesar: {short_name}", callback_data=f"process_{task_id}"),
            InlineKeyboardButton("🗑️ Descartar", callback_data=f"delete_{task_id}")
        ])
        
    # Añadir botones globales si hay tareas en la lista
    if tasks:
        keyboard.append([
            InlineKeyboardButton("✨ Procesar Todo (Automático)", callback_data="process_all"),
            InlineKeyboardButton("💥 Limpiar Todo", callback_data="delete_all")
        ])
        
    return InlineKeyboardMarkup(keyboard)