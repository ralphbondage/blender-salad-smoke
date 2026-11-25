# Report which files a scene needs from outside the .blend, and optionally write a self-contained copy.
#   blender -b SCENE.blend --python prepare_scene.py                      # report only, nothing is written
#   blender -b SCENE.blend --python prepare_scene.py -- --pack OUT.blend  # pack externals, save a COPY to OUT.blend
# The source .blend is never saved. Packing only touches the copy.
import os
import sys

import bpy

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out = args[args.index("--pack") + 1] if "--pack" in args else None


def mb(n):
    return "%.1f MB" % (n / 1e6)


ext_ok, ext_missing, packed = [], [], 0
for img in bpy.data.images:
    if img.source not in ("FILE", "SEQUENCE", "MOVIE", "TILED"):
        continue
    if img.packed_file or img.packed_files:
        packed += 1
        continue
    if not img.filepath:
        continue
    p = bpy.path.abspath(img.filepath)
    (ext_ok if os.path.exists(p) else ext_missing).append((img.name, p))

libs = [(l.name, bpy.path.abspath(l.filepath)) for l in bpy.data.libraries]
others = []
for coll, label in ((bpy.data.fonts, "font"), (bpy.data.sounds, "sound"), (bpy.data.movieclips, "movieclip"),
                    (bpy.data.cache_files, "cache")):
    for d in coll:
        if getattr(d, "packed_file", None):
            continue
        fp = getattr(d, "filepath", "")
        if fp and not fp.startswith("<builtin>"):
            others.append((label, bpy.path.abspath(fp)))

print("PREPARE images: %d packed, %d external present, %d external MISSING" % (packed, len(ext_ok), len(ext_missing)))
size = 0
for name, p in ext_ok:
    s = os.path.getsize(p)
    size += s
    print("PREPARE   external ok     %-40s %s  %s" % (name[:40], mb(s), p))
for name, p in ext_missing:
    print("PREPARE   external MISSING %-39s %s" % (name[:40], p))
print("PREPARE external files present total: %s" % mb(size))
for n, p in libs:
    print("PREPARE   linked library %s -> %s (%s)" % (n, p, "present" if os.path.exists(p) else "MISSING"))
for label, p in others:
    print("PREPARE   external %s -> %s (%s)" % (label, p, "present" if os.path.exists(p) else "MISSING"))
print("PREPARE camera=%s frames %d..%d engine=%s" % (
    bpy.context.scene.camera.name if bpy.context.scene.camera else None,
    bpy.context.scene.frame_start, bpy.context.scene.frame_end, bpy.context.scene.render.engine))

if out:
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out), copy=True, compress=True)
    print("PREPARE wrote self-contained copy: %s (%s)" % (out, mb(os.path.getsize(out))))
