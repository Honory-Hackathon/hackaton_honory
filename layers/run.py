"""Entry point: MODE = points | scene | frames (SOURCE=t02|solweig|static) | manifest."""
import os
import sys

from common import log

if __name__ == "__main__":
    mode = os.environ.get("MODE") or (sys.argv[1] if len(sys.argv) > 1 else "manifest")
    if len(sys.argv) > 2:
        os.environ["SOURCE"] = sys.argv[2]
    log("MODE", mode, os.environ.get("SOURCE"))
    if mode == "points":
        import points
        points.run()
    elif mode == "enrich":
        import points
        points.enrich_with_solweig()
    elif mode == "scene":
        import scene
        scene.run()
    elif mode == "frames":
        import frames
        frames.main()
    if mode in ("manifest", "points", "scene", "frames", "enrich"):
        import manifest
        manifest.build()
