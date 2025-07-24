import bpy
import os
import json
import math
import mathutils
import xml.etree.ElementTree as ET

from bpy.props import StringProperty, CollectionProperty
from . import hson_template

class OBJECT_OT_import_xml(bpy.types.Operator):
    bl_idname = "import_scene.xml"
    bl_label  = "Import XML (.set.xml / .path.xml)"
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(name="Import Directory", subtype='DIR_PATH')
    files:     CollectionProperty(type=bpy.types.OperatorFileListElement)

    _objects_to_process = []
    _timer              = None
    _batch_size         = 25
    _imported_objects   = {}
    _asset_dict         = {}
    _template_data      = None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        self._objects_to_process.clear()
        self._imported_objects.clear()
        for f in self.files:
            name_lower = f.name.lower()
            fp = os.path.join(self.directory, f.name)
            if name_lower.endswith(".path.xml"):
                try:
                    self.import_path_xml(fp, context)
                except Exception as e:
                    self.report({'WARNING'}, f"Failed to import path '{f.name}': {e}")
                continue
            if not name_lower.endswith(".set.xml"):
                continue

            try:
                tree = ET.parse(fp)
                root = tree.getroot()
            except Exception as e:
                self.report({'WARNING'}, f"Failed to parse '{f.name}': {e}")
                continue

            coll_name = os.path.splitext(f.name)[0]
            for hobj in self.convert_xml_to_hson(root):
                self._objects_to_process.append((hobj, coll_name))

        if not self._objects_to_process:
            self.report({'INFO'}, "XML import complete (no .set.xml files queued for batch).")
            return {'FINISHED'}
        addon_root = __name__.split('.')[0]
        prefs = context.preferences.addons[addon_root].preferences
        asset_libraries = bpy.context.preferences.filepaths.asset_libraries
        self.report({'INFO'}, "Using Blender asset libraries for asset processing.")
        for lib in asset_libraries:
            try:
                for root_dir, _, files_list in os.walk(lib.path):
                    for fn in files_list:
                        if fn.lower().endswith(".blend"):
                            blend_fp = os.path.join(root_dir, fn)
                            try:
                                with bpy.data.libraries.load(blend_fp, link=False) as (src, dst):
                                    for name in src.objects:
                                        self._asset_dict.setdefault(name, blend_fp)
                            except Exception:
                                pass
            except Exception:
                pass
        tmpl = context.scene.hson_game_template
        if tmpl:
            addon_root = __name__.split('.')[0]
            prefs = context.preferences.addons[addon_root].preferences
            for d in (prefs.templates_path or "", prefs.debug_templates_path or ""):
                candidate = os.path.join(d, tmpl)
                if os.path.isfile(candidate):
                    try:
                        self._template_data = json.load(open(candidate, 'r'))
                    except Exception:
                        pass
                    break
        context.window_manager.modal_handler_add(self)
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        return {'RUNNING_MODAL'}

    def import_path_xml(self, filepath, context):
        import bpy
        import os
        import mathutils
        import math
        import xml.etree.ElementTree as ET

        tree = ET.parse(filepath)
        root = tree.getroot()

        geometry_map = {}
        for geom in root.findall(".//library/geometry"):
            geom_id = geom.get("id")
            if not geom_id:
                continue
            spline_elems = geom.findall(".//spline3d")
            if spline_elems:
                geometry_map[geom_id] = spline_elems

        if not geometry_map:
            raise RuntimeError("No <geometry> elements with <spline3d> found in the .path.xml.")
        nodes = root.findall(".//scene/node")
        if not nodes:
            raise RuntimeError("No <node> elements found under <scene> in the .path.xml.")

        base_name = os.path.splitext(os.path.basename(filepath))[0]
        curve_counter = 0

        for node in nodes:
            translate_node = node.find("translate")
            if translate_node is not None and translate_node.text:
                tx, ty, tz = [float(c) for c in translate_node.text.strip().split()]
                node_translate = mathutils.Vector((tx, -tz, ty))
            else:
                node_translate = mathutils.Vector((0.0, 0.0, 0.0))
            inst = node.find("instance")
            if inst is None:
                continue
            url = inst.get("url", "")
            if not url.startswith("#"):
                continue
            geom_id = url[1:]
            if geom_id not in geometry_map:
                continue

            spline_list = geometry_map[geom_id]
            if not spline_list:
                continue

            curve_name = f"{base_name}_{geom_id}_{curve_counter:03d}"
            curve_data = bpy.data.curves.new(name=curve_name, type='CURVE')
            curve_data.dimensions = '3D'

            for spline3d in spline_list:
                knots = spline3d.findall("knot")
                knot_count = len(knots)
                if knot_count == 0:
                    continue

                spline = curve_data.splines.new('BEZIER')
                spline.bezier_points.add(knot_count - 1)
                for i, knot in enumerate(knots):
                    point_elem = knot.find("point")
                    if point_elem is None or not point_elem.text:
                        continue
                    coords = [float(s) for s in point_elem.text.strip().split()]
                    pt = mathutils.Vector(coords)
                    bp = spline.bezier_points[i]
                    bp.co = pt
                    bp.handle_left  = pt
                    bp.handle_right = pt
                    bp.handle_left_type  = 'FREE'
                    bp.handle_right_type = 'FREE'
            curve_obj = bpy.data.objects.new(curve_name, curve_data)
            context.collection.objects.link(curve_obj)
            curve_obj.location = node_translate
            curve_obj.rotation_mode = 'XYZ'
            curve_obj.rotation_euler = mathutils.Euler((math.radians(90), 0.0, 0.0), 'XYZ')

            curve_counter += 1

        self.report({'INFO'}, f"Imported {curve_counter} curves from '{os.path.basename(filepath)}'.")
    def convert_xml_to_hson(self, root):
        out = []
        for child in root:
            sid = child.findtext("SetObjectID")
            if not sid:
                continue
            obj_type = child.tag.strip()
            d = {
                "id":       f"{{{sid}}}",
                "name":     f"{obj_type}.{sid}",
                "type":     obj_type,
                "position": [
                    float(child.findtext("Position/x", 0.0)),
                    -float(child.findtext("Position/z", 0.0)),
                    float(child.findtext("Position/y", 0.0))
                ],
                "rotation": [
                    float(child.findtext("Rotation/w", 1.0)),
                    float(child.findtext("Rotation/x", 0.0)),
                    float(child.findtext("Rotation/y", 0.0)),
                    float(child.findtext("Rotation/z", 0.0))
                ],
                "scale":      [1.0, 1.0, 1.0],
                "parameters": {}
            }

            for elem in child:
                if elem.tag in {"SetObjectID", "Position", "Rotation", "ParentId"}:
                    continue
                if elem.tag == "MultiSetParam":
                    elements_list = []
                    for e in elem.findall("Element"):
                        idx_node = e.find("Index")
                        idx = idx_node.text.strip() if idx_node is not None else ""

                        # Position.x, y, z
                        pos_node = e.find("Position")
                        px = float(pos_node.findtext("x", "0"))
                        py = float(pos_node.findtext("y", "0"))
                        pz = float(pos_node.findtext("z", "0"))

                        # Rotation.w, x, y, z
                        rot_node = e.find("Rotation")
                        rw = float(rot_node.findtext("w", "1"))
                        rx = float(rot_node.findtext("x", "0"))
                        ry = float(rot_node.findtext("y", "0"))
                        rz = float(rot_node.findtext("z", "0"))

                        elements_list.append({
                            "Index":    idx,
                            "Position": {"x": px, "y": py, "z": pz},
                            "Rotation": {"w": rw, "x": rx, "y": ry, "z": rz}
                        })
                    msp_dict = {
                        "Elements":     elements_list,
                        "BaseLine":     elem.findtext("BaseLine", "0"),
                        "Count":        elem.findtext("Count", "1"),
                        "Direction":    elem.findtext("Direction", "0"),
                        "Interval":     elem.findtext("Interval", "0"),
                        "IntervalBase": elem.findtext("IntervalBase", "0"),
                        "PositionBase": elem.findtext("PositionBase", "0"),
                        "RotationBase": elem.findtext("RotationBase", "0"),
                    }
                    d["parameters"]["MultiSetParam"] = msp_dict
                    continue
                if len(elem):
                    d["parameters"][elem.tag] = {c.tag: c.text or "" for c in elem}
                else:
                    d["parameters"][elem.tag] = elem.text or ""
            pid = child.findtext("ParentId")
            if pid:
                d["parentId"] = pid.strip()
            out.append(d)
        return out

    def create_collection(self, name, context):
        if name in bpy.data.collections:
            return bpy.data.collections[name]
        coll = bpy.data.collections.new(name)
        context.scene.collection.children.link(coll)
        return coll

    def process_batch(self, context):
        def flatten(d, pk="", sep=""):
            flat = {}
            for k, v in d.items():
                nk = f"{pk}{sep}{k}" if pk else k
                if isinstance(v, dict):
                    flat.update(flatten(v, nk, sep))
                else:
                    flat[nk] = v
            return flat
        count = 0

        while self._objects_to_process and count < self._batch_size:
            obj_dict, coll_name = self._objects_to_process.pop(0)
            coll = self.create_collection(coll_name, context)
            new_obj = None
            atype = obj_dict["type"]
            if atype in self._asset_dict:
                try:
                    with bpy.data.libraries.load(self._asset_dict[atype], link=False) as (src, dst):
                        if atype in src.objects:
                            dst.objects = [atype]
                    if dst.objects:
                        tmp = dst.objects[0]
                        tmp.name = obj_dict["name"]
                        new_obj = tmp.copy()
                        new_obj.data = tmp.data.copy()
                        if new_obj.asset_data:
                            new_obj.asset_data_clear()
                        bpy.data.objects.remove(tmp, do_unlink=True)
                except Exception:
                    new_obj = None

            if not new_obj:
                bpy.ops.mesh.primitive_cube_add(size=0.5)
                new_obj = context.active_object
                new_obj.name = obj_dict["name"]
                for C in new_obj.users_collection:
                    C.objects.unlink(new_obj)
                coll.objects.link(new_obj)

            else:
                for C in new_obj.users_collection:
                    C.objects.unlink(new_obj)
                coll.objects.link(new_obj)

            if new_obj.data:
                new_obj.data.name = new_obj.name
            new_obj.hson_uuid = obj_dict.get("id", "")
            if "parentId" in obj_dict:
                new_obj["parentId"] = obj_dict["parentId"]

            new_obj.location = obj_dict["position"]
            q = mathutils.Quaternion(obj_dict["rotation"])
            T = mathutils.Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))
            M = T @ q.to_matrix() @ T.inverted()
            new_obj.rotation_mode  = 'XYZ'
            new_obj.rotation_euler = M.to_euler('XYZ')
            new_obj.scale          = obj_dict["scale"]

            if self._template_data:
                flat = flatten(obj_dict["parameters"])
                ok, msg, *rest = hson_template.apply_hson_template_to_object(
                    new_obj, self._template_data, imported_params=flat
                )
                if not ok:
                    self.report({'WARNING'}, f"Template apply failed: {msg}")
            self._imported_objects[new_obj.hson_uuid] = new_obj
            msp = obj_dict["parameters"].get("MultiSetParam")
            if msp:
                elements_list = msp.get("Elements", [])
                if elements_list:
                    for elem_dict in elements_list:
                        pos = elem_dict["Position"]
                        rot = elem_dict["Rotation"]
                        dup = new_obj.copy()
                        dup.data = new_obj.data.copy()
                        dup.location = mathutils.Vector((pos["x"], -pos["z"], pos["y"]))
                        q_elem = mathutils.Quaternion((rot["w"], rot["x"], rot["y"], rot["z"]))
                        T_elem = mathutils.Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))
                        M_elem = T_elem @ q_elem.to_matrix() @ T_elem.inverted()
                        dup.rotation_mode  = 'XYZ'
                        dup.rotation_euler = M_elem.to_euler('XYZ')
                        for C in dup.users_collection:
                            C.objects.unlink(dup)
                        coll.objects.link(dup)
                        idx_str = elem_dict.get("Index", "")
                        key = f"{new_obj.hson_uuid}_idx{idx_str}"
                        self._imported_objects[key] = dup
                else:
                    base      = float(msp.get("BaseLine",    0.0))
                    cnt       = int(  msp.get("Count",       1))
                    dir_deg   = float(msp.get("Direction",   0.0))
                    iv        = float(msp.get("Interval",    0.0))
                    dr        = math.radians(dir_deg)
                    orig      = new_obj.location.copy()

                    for i in range(1, cnt):
                        off = base + i * iv
                        v = mathutils.Vector((0, -off * math.cos(dr), 0))
                        dup = new_obj.copy()
                        dup.data = new_obj.data.copy()
                        dup.location = orig + v
                        q_base = mathutils.Quaternion(obj_dict["rotation"])
                        T_base = mathutils.Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))
                        M_base = T_base @ q_base.to_matrix() @ T_base.inverted()
                        dup.rotation_mode  = 'XYZ'
                        dup.rotation_euler = M_base.to_euler('XYZ')
                        for C in dup.users_collection:
                            C.objects.unlink(dup)
                        coll.objects.link(dup)
                        self._imported_objects[f"{new_obj.hson_uuid}_{i}"] = dup
            count += 1

    def perform_parenting(self):
        for o in self._imported_objects.values():
            pid = o.get("parentId")
            if pid and pid in self._imported_objects:
                p = self._imported_objects[pid]
                o.parent = p
                o.matrix_parent_inverse = p.matrix_world.inverted()

    def modal(self, context, event):
        if event.type == 'TIMER':
            self.process_batch(context)
            if not self._objects_to_process:
                context.window_manager.event_timer_remove(self._timer)
                self.perform_parenting()
                self.report({'INFO'}, "XML import complete.")
                return {'FINISHED'}
        return {'PASS_THROUGH'}

def register():
    bpy.utils.register_class(OBJECT_OT_import_xml)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_import_xml)
