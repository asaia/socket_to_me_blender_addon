bl_info = {
    "name": "Socket To Me",
    "author": "Andy Saia",
    "version": (1, 0),
    "blender": (3, 6, 2),
    "description": "Tool to place modular assets based on and in and out socket",
}

if "bpy" in locals():
    import importlib
    importlib.reload(socket_to_me)
else:
    from . import socket_to_me

def register():
    socket_to_me.register()

def unregister():
    socket_to_me.unregister()

if __name__ == "__main__":
    register()
