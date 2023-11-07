from dataclasses import dataclass, field
from typing import List, Optional, Tuple
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

@dataclass
class ModularAssetData:
    collection:bpy.types.Collection
    in_socket:mathutils.Matrix = mathutils.Matrix.Identity(4)
    out_sockets:list[mathutils.Matrix] = field(default_factory=list)

@dataclass
class SocketData:
    transform:mathutils.Matrix = mathutils.Matrix.Identity(4)
    is_highlighted = False
    out_sockets:List['SocketData'] = field(default_factory=list)
    collection_instance:Optional[bpy.types.Object] = None

def create_modular_asset_from_collection(collection:bpy.types.Collection):
    in_socket_objects = {obj.name: obj for obj in collection.objects if obj.name.startswith("IN_")}
    in_socket = list(in_socket_objects.values())[0]
    out_socket_objects = {obj.name: obj for obj in collection.objects if obj.name.startswith("OUT_")}
    out_sockets = [value.matrix_local for value in out_socket_objects.values()]
    return ModularAssetData(collection=collection, in_socket=in_socket.matrix_local, out_sockets=out_sockets)

def create_instance_at_socket(socket:SocketData, modular_asset:ModularAssetData) -> bpy.types.Object:
    instance_transform:mathutils.Matrix = socket.transform @ modular_asset.in_socket.inverted_safe()
    instance = bpy.data.objects.new(modular_asset.collection.name, None)
    instance.instance_type = "COLLECTION"
    bpy.context.collection.objects.link(instance)
    instance.instance_collection = modular_asset.collection
    instance.matrix_world = instance_transform
    return instance

def create_sockets_from_modular_asset(world_transform:mathutils.Matrix, modular_asset:ModularAssetData):
    pivot = modular_asset.in_socket.inverted_safe()
    sockets = [SocketData(world_transform @ pivot @ local_transform) for local_transform in modular_asset.out_sockets]
    return sockets

def for_each_socket(socket:SocketData, function):
    function(socket)
    if socket.out_sockets:
        for socket in socket.out_sockets:
            for_each_socket(socket, function)

def draw(self, context, uv_sphere_verts):
    shader:typing.Any = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
    gpu.state.depth_test_set('LESS_EQUAL')

    def draw_socket(socket:SocketData):
        # Don't draw sockets that already have an instance
        if socket.collection_instance is not None:
            return

        scale = HIGHLIGHTED_RADIUS if socket.is_highlighted else SOCKET_RADIUS
        sphere_verts = [socket.transform.to_translation() + (v * scale) for v in uv_sphere_verts]
        batch:gpu.types.GPUBatch = batch_for_shader(shader, 'TRIS', {"pos": sphere_verts})

        color = HIGHLIGHTED_COLOR if socket.is_highlighted else SOCKET_COLOR
        shader.uniform_float("color", color)
        batch.draw(shader)

    for_each_socket(self.root_socket, draw_socket)

class SocketToMeModalOperator(bpy.types.Operator):
    """Click on a socket to spawn a random module. Right click to change module instance"""
    bl_idname = "object.modal_socket_to_me"
    bl_options = {'REGISTER', 'UNDO'}
    bl_label = "Socket to me tool"

    def __init__(self):
        self.root_socket:Optional[SocketData] = None
        self.last_clicked_socket:Optional[SocketData] = None
        self.modular_assets:List[ModularAssetData]
        self.draw_handle = None

    def modal(self, context, event):
        context.area.tag_redraw()
        
        if self.root_socket is None:
            return {'CANCELLED'}

        if event.type in {'ESC'}:
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle, 'WINDOW')
            return {'CANCELLED'}

        # camera location and camera to mouse ray
        world_space_mouse_position:mathutils.Vector = bpy_extras.view3d_utils.region_2d_to_location_3d(context.region, context.space_data.region_3d, (event.mouse_region_x, event.mouse_region_y), (0, 0, 0))
        camera_view_position:mathutils.Vector = context.space_data.region_3d.view_matrix.inverted().translation
        camera_through_mouse_position_ray:mathutils.Vector = (world_space_mouse_position - camera_view_position).normalized()
        
        # find closest socket by sorting the angle between the socket to the camera and mouse to camera
        closest_socket:Optional[SocketData] = None
        closest_length:float = -1.0
        def find_closest_socket_to_mouse_ray(socket:SocketData):
            nonlocal closest_socket, closest_length
            # Don't include sockets that already have an instance
            if socket.collection_instance is not None:
                return
            socket.is_highlighted = False
            
            socket_position = socket.transform.to_translation()
            socket_to_camera_ray_foo = (socket_position - camera_view_position)
            projected_vector = socket_to_camera_ray_foo.project(camera_through_mouse_position_ray)
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
            
        if event.type in {'RIGHTMOUSE'}:
            if event.value == 'PRESS' and self.last_clicked_socket and self.last_clicked_socket.collection_instance:
                random_module = random.choice(self.modular_assets)
                self.last_clicked_socket.collection_instance.instance_collection = random_module.collection
                self.last_clicked_socket.collection_instance.matrix_world = self.last_clicked_socket.transform @ random_module.in_socket.inverted_safe()
                self.last_clicked_socket.out_sockets = create_sockets_from_modular_asset(self.last_clicked_socket.transform, random_module)
                return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        if context.area.type == 'VIEW_3D':            
            # initilize modular assets
            pipe_collection_names:list[str] = ["pipe_T", "pipeStraight", "pipe_bent", "pipe_bent_02", "pipe_handle", "pipe_u"]
            self.modular_assets = [create_modular_asset_from_collection(bpy.data.collections.get(collection_name)) for collection_name in pipe_collection_names]

            # triangulate the built-in uv sphere to draw for each socket
            new_bmesh:bmesh.types.BMesh = bmesh.new()
            bmesh.ops.create_uvsphere(new_bmesh, u_segments= 6, v_segments=4, radius=1)
            bmesh.ops.triangulate(new_bmesh, faces=new_bmesh.faces)
            uv_sphere_verts = [mathutils.Vector(v.co.to_tuple()) for f in new_bmesh.faces for v in f.verts]

            # spawn starting module
            self.root_socket = SocketData(mathutils.Matrix.Identity(4))
            start_module = self.modular_assets[0]
            self.root_socket.collection_instance = create_instance_at_socket(self.root_socket, start_module)
            self.root_socket.out_sockets = create_sockets_from_modular_asset(self.root_socket.transform, start_module)
            self.last_clicked_socket = self.root_socket
            
            # setup draw handler
            args = (self, context, uv_sphere_verts)
            self.draw_handle = bpy.types.SpaceView3D.draw_handler_add(draw, args, 'WINDOW', 'POST_VIEW')

            context.window_manager.modal_handler_add(self)

            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "View3D not found, cannot run operator")
            return {'CANCELLED'}

def menu_func(self, context):
    self.layout.operator(SocketToMeModalOperator.bl_idname, text="Socket To Me")

# Register and add to the "view" menu (required to also use F3 search "Modal Draw Operator" for quick access).
def register():
    bpy.utils.register_class(SocketToMeModalOperator)
    bpy.types.VIEW3D_MT_object.append(menu_func)


def unregister():
    bpy.utils.unregister_class(SocketToMeModalOperator)
    bpy.types.VIEW3D_MT_object.remove(menu_func)

if __name__ == "__main__":
    register()
