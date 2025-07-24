bl_info = {
    "name": "HE2 Toolbox: Blender Edition",
    "author": "SeasideRanger",
    "version": (0, 5, 8),
    "blender": (4, 5, 0),
    "description": (
        "A collection of tools for modding Sonic Frontiers/Shadow Generations."
    )
}

import bpy, os, json, re, uuid
from bpy_extras import view3d_utils
from .modules import (
    hson_template,
    hson_module,
    xml_module,
    svcol_module,
    fxcol_module,
    blendhog_addon
)

bpy.types.Object.hson_uuid = bpy.props.StringProperty(name="HSON UUID", default="")

def update_enum_selection(self, context):
    try:
        obj = context.object
        parent_param = None
        for param in obj.hson_parameters:
            if "enum" in param.name:
                for item in param.enum_items:
                    if item == self:
                        parent_param = param
                        break
            if parent_param:
                break
        if parent_param:
            for item in parent_param.enum_items:
                if item != self and self.selected:
                    item.selected = False
            if self.selected:
                parent_param.string_value = self.value
    except Exception as e:
        pass

class HSONParameterListItem(bpy.types.PropertyGroup):
    selected: bpy.props.BoolProperty(
        name="Selected",
        update=update_enum_selection
    )
    value: bpy.props.StringProperty(name="Value")

class HSONParametersListValues(bpy.types.PropertyGroup):
    liststring: bpy.props.StringProperty(name="")
    listint: bpy.props.IntProperty(name="List Int")
    listfloat: bpy.props.FloatProperty(name="List Float")
    listobject: bpy.props.PointerProperty(name="Object ID", type=bpy.types.Object)

class HSONParametersPropertyGroup(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    float_value: bpy.props.FloatProperty(name="Float Value", default=0.0)
    int_value: bpy.props.IntProperty(name="Integer Value", default=0)
    bool_value: bpy.props.BoolProperty(name="Bool Value", default=False)
    vector_value: bpy.props.FloatVectorProperty(
        name="Vector Value", size=3, default=(0.0, 0.0, 0.0)
    )
    string_value: bpy.props.StringProperty(name="String Value", default="")
    object_value: bpy.props.PointerProperty(name="Object", type=bpy.types.Object)
    list_value: bpy.props.CollectionProperty(type=HSONParametersListValues)
    enum_items: bpy.props.CollectionProperty(type=HSONParameterListItem)
    expanded: bpy.props.BoolProperty(name="Expanded", default=False)
    description: bpy.props.StringProperty(name="Description", default="")

class OBJECT_OT_GenerateUUID(bpy.types.Operator):
    bl_idname = "object.generate_hson_uuid"
    bl_label = "New UUID"
    bl_description = "Generate a new HSON UUID for the active object"

    def execute(self, context):
        obj = context.object
        if obj:
            obj.hson_uuid = "{" + str(uuid.uuid4()).upper() + "}"
            self.report({'INFO'}, "Generated new UUID")
        else:
            self.report({'WARNING'}, "No active object found")
        return {'FINISHED'}

class OBJECT_PT_HSONProperties(bpy.types.Panel):
    bl_label = "HSON Properties"
    bl_idname = "OBJECT_PT_hson_properties"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        obj = context.object
        if not obj:
            return
        row = layout.row(align=True)
        row.prop(obj, "hson_uuid", text="ID")
        row.operator("object.generate_hson_uuid", text="", icon="FILE_REFRESH")
        layout.operator("object.apply_hson_template", text="Apply Template")
        layout.prop(context.scene, "hson_game_template", text="HSON Template")
        layout.label(text=f"Object Name: {obj.name}")
        layout.label(text=f"Object Type: {obj.data.name if hasattr(obj, 'data') and obj.data else 'N/A'}")
        layout.separator()
        for i, param in enumerate(obj.hson_parameters):
            parts = param.name.split(':')
            base_name = ":".join(parts[:-1])
            if param.description:
                base_name += f" ({param.description})"
            data_type = parts[-1]
            if data_type == "object_reference":
                layout.prop(param, "object_value", text=base_name)
            elif data_type == "enum":
                box = layout.box()
                row = box.row()
                icon = 'TRIA_DOWN' if param.expanded else 'TRIA_RIGHT'
                op = row.operator("wm.context_toggle", text=f"{base_name}: {param.string_value}", emboss=False, icon=icon)
                op.data_path = f"object.hson_parameters[{i}].expanded"
                if param.expanded:
                    for enum_item in param.enum_items:
                        row = box.row()
                        row.prop(enum_item, "selected", text=enum_item.value)
            elif data_type == "float":
                layout.prop(param, "float_value", text=base_name)
            elif data_type == "int":
                layout.prop(param, "int_value", text=base_name)
            elif data_type == "bool":
                layout.prop(param, "bool_value", text=base_name)
            elif data_type == "vector":
                layout.prop(param, "vector_value", text=base_name)
            elif data_type == "list":
                box = layout.box()
                split = box.split(factor=0.8)
                col = split.column()
                col.label(text=base_name)
                for li in param.list_value:
                    row = col.row()
                    row.prop(li, "liststring", text="")
                    row.prop(li, "listobject", text="")
                col = split.column(align=True)
                col.operator("object.add_hson_list_item", text="", icon="ADD").index = i
                col.operator("object.remove_hson_list_item", text="", icon="REMOVE").index = i
                split = split.split(factor=0.6666)
                col = split.column()
                col.scale_y = 2.0
                col.operator("object.add_hson_selected", text="", icon="SELECT_SET").index = i
            elif data_type == "string":
                row = layout.row(align=True)
                row.prop(param, "string_value", text=base_name)
            else:
                row = layout.row(align=True)
                row.prop(param, "string_value", text=base_name)
                row.operator("object.hson_eyedrop", text="", icon="EYEDROPPER").index = i
        layout = self.layout
        obj = context.object
        if not obj:
            return

class OBJECT_OT_AddHSONListItem(bpy.types.Operator):
    bl_idname = "object.add_hson_list_item"
    bl_label = "Add HSON List Item"
    index: bpy.props.IntProperty()

    def execute(self, context):
        context.object.hson_parameters[self.index].list_value.add()
        return {'FINISHED'}

class OBJECT_OT_RemoveHSONListItem(bpy.types.Operator):
    bl_idname = "object.remove_hson_list_item"
    bl_label = "Remove HSON List Item"
    index: bpy.props.IntProperty()

    def execute(self, context):
        param = context.object.hson_parameters[self.index]
        if len(param.list_value) > 0:
            param.list_value.remove(len(param.list_value) - 1)
        return {'FINISHED'}

class OBJECT_OT_AddHSONSelected(bpy.types.Operator):
    bl_idname = "object.add_hson_selected"
    bl_label = "Add All Selected Objects"
    index: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.object
        param = obj.hson_parameters[self.index]
        for o in [o for o in context.selected_objects if o != obj]:
            new_item = param.list_value.add()
            new_item.listobject = o
        return {'FINISHED'}

class VIEW3D_PT_he2_tools(bpy.types.Panel):
    bl_label = "HSON Module"
    bl_idname = "VIEW3D_PT_he2_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "HE2 Tools"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.prop(context.scene, "hson_game_template", text="HSON Template")
        layout.operator("import_scene.hson", text="Import HSON")
        layout.operator("import_scene.xml", text="Import XML")

class HSONAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    templates_path: bpy.props.StringProperty(
        name="Templates Folder",
        subtype='DIR_PATH',
        description="Directory for HSON templates",
        default="",
        update=lambda self, context: bpy.ops.scene.reload_hson_templates()
    )

    debug_templates_path: bpy.props.StringProperty(
        name="Additional templates (debug)",
        subtype='DIR_PATH',
        description="Directory for test templates",
        default="",
        update=lambda self, context: bpy.ops.scene.reload_hson_templates()
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "templates_path")
        layout.prop(self, "debug_templates_path")

common_classes = [
    HSONParameterListItem,
    HSONParametersListValues,
    HSONParametersPropertyGroup,
    OBJECT_PT_HSONProperties,
    OBJECT_OT_AddHSONListItem,
    OBJECT_OT_RemoveHSONListItem,
    OBJECT_OT_AddHSONSelected,
    OBJECT_OT_GenerateUUID,
    VIEW3D_PT_he2_tools,
    HSONAddonPreferences
]

def register():
    for cls in common_classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.hson_game_template = bpy.props.EnumProperty(
        name="HSON Templates",
        description="Select a HSON template",
        items=hson_template.get_templates
    )
    bpy.types.Object.hson_parameters = bpy.props.CollectionProperty(
        type=HSONParametersPropertyGroup
    )
    
    hson_template.register()
    hson_module.register()
    xml_module.register()
    svcol_module.register()
    fxcol_module.register()
    blendhog_addon.register()

def unregister():
    for cls in reversed(common_classes):
        bpy.utils.unregister_class(cls)

    del bpy.types.Scene.hson_game_template
    del bpy.types.Object.hson_parameters

    hson_template.unregister()
    hson_module.unregister()
    xml_module.unregister()
    svcol_module.unregister()
    fxcol_module.unregister()
    blendhog_addon.unregister()

if __name__ == "__main__":
    register()
