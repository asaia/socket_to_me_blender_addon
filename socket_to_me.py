"""
This file contains the Blender modal operator and associated data structures used for spawning modular connected asset instances.

The addon works by defining an "in" connection and a list of "out" connections for a set of collections. 
The tool will randomly instance a collection onto the out connections of a previous instance.
This is useful for creating networks of connected objects such as pipes, corridors, roads, and more.

To use:
    * Create a parent collection named "modular_assets".
    * Inside the parent collection, place collections you'd like to instance.
    * Ensure that the collections have one Empty object with the prefix IN_ and as many OUT_ empty objects as you need.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional
import random
import typing
import bmesh
import bpy
import bpy_extras
import gpu
from gpu_extras.batch import batch_for_shader
import mathutils

SOCKET_COLOR = (0, 0.6, 1, 0.1)
HIGHLIGHTED_COLOR = (0.5, 0.8, 1, 0.3)
SOCKET_RADIUS = 0.15
HIGHLIGHTED_RADIUS = 0.2
MODULAR_ASSETS_CONTAINER_NAME = "modular_assets"
SOCKET_OUTPUT_PREFIX = "OUT_"
SOCKET_IN_PREFIX = "IN_"

@dataclass(slots=True)
class ModularAssetData:
    """
    ModularAssets contain the information necessary to create a collection instance at a socket.
    You can think of the in_socket as the origin from where an asset will be spawned.
    The out sockets are the local space transforms that determine where the next modules can be spawned.
    """
    collection:bpy.types.Collection
    in_socket:mathutils.Matrix = mathutils.Matrix.Identity(4)
    out_sockets:list[mathutils.Matrix] = field(default_factory=list)

@dataclass(slots=True)
class SocketData:
    """
    Sockets are where modular assets can be instanced.
    They contain a world space transform as well as a list of child sockets.
    You can think of sockets as nodes in a graph.
    Given a root socket, you can traverse the graph to all connected sockets.
    """
    transform:mathutils.Matrix = mathutils.Matrix.Identity(4)
    is_highlighted:bool = field(default=False, init=True)
    out_sockets:List['SocketData'] = field(default_factory=list)
    collection_instance:Optional[bpy.types.Object] = None

def create_modular_asset_from_collection(collection:bpy.types.Collection) -> ModularAssetData:
    """
    Creates a modular asset from the provided collection. It does this by looping through the collection's children 
    and storing transforms on any child objects that matches the IN_ or OUT_ prefixes

    Args:
        collection (bpy.types.Collection): The collection to turn into a modular asset

    Returns:
        ModularAssetData: The newly created modular asset containing information for the in and out socket transforms
    """
    in_socket_objects = {obj.name: obj for obj in collection.objects if obj.name.startswith(SOCKET_IN_PREFIX)}
    # if no socket is found the default is the collection's pivot
    in_socket = mathutils.Matrix.Identity(4)
    if in_socket_objects:
        in_socket = list(in_socket_objects.values())[0].matrix_local
    out_socket_objects = {obj.name: obj for obj in collection.objects if obj.name.startswith(SOCKET_OUTPUT_PREFIX)}
    out_sockets = [value.matrix_local for value in out_socket_objects.values()]
    return ModularAssetData(collection = collection, in_socket = in_socket, out_sockets = out_sockets)

def create_instance_at_socket(socket:SocketData, modular_asset:ModularAssetData) -> bpy.types.Object:
    """
    Create an instance of a modular asset at the specified socket.

    Args:
        socket (SocketData): The socket where the instance will be created.
        modular_asset (ModularAssetData): The modular asset to instance.

    Returns:
        bpy.types.Object: A reference to the created instance.
    """
    instance_transform:mathutils.Matrix = socket.transform @ modular_asset.in_socket.inverted_safe()
    instance = bpy.data.objects.new(modular_asset.collection.name, None)
    instance.instance_type = "COLLECTION"
    bpy.context.collection.objects.link(instance)
    instance.instance_collection = modular_asset.collection
    instance.matrix_world = instance_transform
    return instance

def create_sockets_from_modular_asset(world_transform:mathutils.Matrix, modular_asset:ModularAssetData) -> List[SocketData]:
    """
    Create a socket for each out socket in the provided modular asset.
    These sockets are created in world space.

    Args:
        world_transform (mathutils.Matrix): The basis transform for the created sockets, relative to world space.
        modular_asset (ModularAssetData): The modular asset containing local space out_socket transforms.

    Returns:
        List[SocketData]: Newly created out sockets positioned in world space.
    """
    pivot = modular_asset.in_socket.inverted_safe()
    sockets = [SocketData(world_transform @ pivot @ local_transform) for local_transform in modular_asset.out_sockets]
    return sockets

def does_socket_have_instance(socket:SocketData) -> bool:
    """
    Sockets are empty until the user clicks on them to spawn an instance
    Only empty sockets are selectable and should be rendered
    Args:
        socket (SocketData): the socket to test
    Returns:
        bool: True if the socket has an instance spawned at its transform
    """
    return socket.collection_instance is not None

def draw_callback(self, uv_sphere_verts:List[mathutils.Vector]):
    if self.root_socket is None:
        return

    shader:typing.Any = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
    gpu.state.depth_test_set('LESS_EQUAL')

    def draw_socket(socket:SocketData):
        if does_socket_have_instance(socket):
            return

        scale = HIGHLIGHTED_RADIUS if socket.is_highlighted else SOCKET_RADIUS
        sphere_verts = [socket.transform.to_translation() + (v * scale) for v in uv_sphere_verts]
        batch:gpu.types.GPUBatch = batch_for_shader(shader, 'TRIS', {"pos": sphere_verts})

        color = HIGHLIGHTED_COLOR if socket.is_highlighted else SOCKET_COLOR
        shader.uniform_float("color", color)
        batch.draw(shader)

    for_each_socket(self.root_socket, draw_socket)

def for_each_socket(socket:SocketData, function:Callable[[SocketData], None]):
    """
    Recursively iterate over all sockets, executing the provided method on each one.

    Args:
        socket (SocketData): The current socket being processed.
        function (Callable[[SocketData], None]): The function to apply to each socket.
    """
    function(socket)
    if socket.out_sockets:
        for socket in socket.out_sockets:
            for_each_socket(socket, function)

class SocketToMeModalOperator(bpy.types.Operator):
    """Click on a socket to spawn a random module. Right click to cycle through module instances"""
    bl_idname = "object.modal_socket_to_me"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Socket to me tool"

    __slots__ = ("root_socket", "last_clicked_socket", "modular_assets", "draw_handle")

    def __init__(self):
        self.root_socket:Optional[SocketData] = None
        self.last_clicked_socket:Optional[SocketData] = None
        self.modular_assets:List[ModularAssetData]
        self.draw_handle = None

    def modal(self, context, event):
        context.area.tag_redraw()

        if event.type in {'ESC'} or self.root_socket is None:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
            return {'CANCELLED'}

        # camera location and camera to mouse ray
        world_space_mouse_position:mathutils.Vector = bpy_extras.view3d_utils.region_2d_to_location_3d(context.region, 
                                                                                                       context.space_data.region_3d, 
                                                                                                       (event.mouse_region_x, event.mouse_region_y), mathutils.Vector())
        camera_view_position:mathutils.Vector = context.space_data.region_3d.view_matrix.inverted().translation
        camera_through_mouse_position_ray:mathutils.Vector = (world_space_mouse_position - camera_view_position).normalized()
        
        # find the closest socket by projecting the camera to object ray to get the closest point along that ray to the object
        # then do a distance check to see if we are within the radius of the object
        closest_socket:Optional[SocketData] = None
        closest_length:float = -1.0
        def find_closest_socket_to_mouse_ray(socket:SocketData):
            nonlocal closest_socket, closest_length
            # don't include sockets that already have an instance
            if does_socket_have_instance(socket):
                return
            socket.is_highlighted = False
            socket_position = socket.transform.to_translation()
            socket_to_camera_ray = (socket_position - camera_view_position)
            projected_vector = socket_to_camera_ray.project(camera_through_mouse_position_ray)
            length_squared = (socket_position - (camera_view_position + projected_vector)).length_squared

            if length_squared < SOCKET_RADIUS and length_squared > closest_length:
                closest_socket = socket
                closest_length = length_squared
    
        for_each_socket(self.root_socket, find_closest_socket_to_mouse_ray)

        if closest_socket is not None:
            closest_socket.is_highlighted = True

        if event.type in {'LEFTMOUSE'}:
            if event.value == 'PRESS' and  closest_socket is not None:
                random_module = random.choice(self.modular_assets)
                closest_socket.collection_instance = create_instance_at_socket(closest_socket, random_module)
                closest_socket.out_sockets = create_sockets_from_modular_asset(closest_socket.transform, random_module)
                self.last_clicked_socket = closest_socket
                return {'RUNNING_MODAL'}

        # cycles through available modules
        if event.type in {'RIGHTMOUSE'}:
            if event.value == 'PRESS' and self.last_clicked_socket and self.last_clicked_socket.collection_instance:
                last_instance = self.last_clicked_socket.collection_instance.instance_collection
                index_of = next((i for (i, asset) in enumerate(self.modular_assets) if asset.collection is last_instance), 0)
                next_index = (index_of + 1) % len(self.modular_assets)
                random_module = self.modular_assets[next_index]

                self.last_clicked_socket.collection_instance.instance_collection = random_module.collection
                self.last_clicked_socket.collection_instance.matrix_world = self.last_clicked_socket.transform @ random_module.in_socket.inverted_safe()
                self.last_clicked_socket.out_sockets = create_sockets_from_modular_asset(self.last_clicked_socket.transform, random_module)
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        if context.area.type == 'VIEW_3D':            
            # initilize modular assets
            modular_asset_container:bpy.types.Collection = bpy.data.collections.get(MODULAR_ASSETS_CONTAINER_NAME)
            if modular_asset_container is None:
                print(f'Could not find assets to instance. Create a parent collection named {MODULAR_ASSETS_CONTAINER_NAME} and put all modular asset collections inside it')
                return {'CANCELLED'}
            self.modular_assets = [create_modular_asset_from_collection(collection) for collection in modular_asset_container.children]

            # triangulate the built-in uv sphere to draw for each socket
            new_bmesh:bmesh.types.BMesh = bmesh.new()
            bmesh.ops.create_uvsphere(new_bmesh, u_segments= 6, v_segments=4, radius=1)
            bmesh.ops.triangulate(new_bmesh, faces = new_bmesh.faces)
            uv_sphere_verts = [mathutils.Vector(v.co.to_tuple()) for f in new_bmesh.faces for v in f.verts]

            # spawn starting module
            self.root_socket = SocketData(mathutils.Matrix.Identity(4))
            start_module = self.modular_assets[0]
            self.root_socket.collection_instance = create_instance_at_socket(self.root_socket, start_module)
            self.root_socket.out_sockets = create_sockets_from_modular_asset(self.root_socket.transform, start_module)
            self.last_clicked_socket = self.root_socket
            
            # setup draw handler
            self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw_callback, (self, uv_sphere_verts), 'WINDOW', 'POST_VIEW')

            context.window_manager.modal_handler_add(self)

            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "View3D not found, cannot run operator")
            return {'CANCELLED'}

def menu_function(self, context):
    self.layout.operator(SocketToMeModalOperator.bl_idname, text="Socket To Me")

def register():
    """
    Register and add to the "view" menu (required to also use F3 search "Socket To Me" for quick access).
    """
    bpy.utils.register_class(SocketToMeModalOperator)
    bpy.types.VIEW3D_MT_object.append(menu_function)


def unregister():
    bpy.utils.unregister_class(SocketToMeModalOperator)
    bpy.types.VIEW3D_MT_object.remove(menu_function)

if __name__ == "__main__":
    register()
