"""Render the 106 landmarks with their indices, zoomed, so the region mapping
can be read off the image instead of assumed."""
import cv2
import numpy as np
from pathlib import Path

out = Path(__file__).resolve().parents[1] / "data" / "selftest"
crop = cv2.imread(str(out / "lmk_crop_192.png"))
pts = np.load(out / "lmk_raw.npy")

# map image-space points back into the 192x192 crop frame for drawing
SIZE = 192


def draw(indices, scale, fname, pad=40):
    img = cv2.resize(crop, (SIZE * scale, SIZE * scale), interpolation=cv2.INTER_CUBIC)
    canvas = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    sel = pts[indices]
    for i, (x, y) in zip(indices, sel):
        # image space -> crop space (crop was bbox-centred, 1.5x, 192px)
        print_ = True
        px, py = int(x * scale) + pad, int(y * scale) + pad
        if print_:
            cv2.circle(canvas, (px, py), 3, (0, 255, 0), -1)
            cv2.putText(canvas, str(i), (px + 3, py - 3),
                        cv2.FONT_HERSHEY_PLAIN, 0.9, (0, 220, 255), 1)
    cv2.imwrite(str(out / fname), canvas)
    print("wrote", fname)


# The crop is 192px covering side=max(w,h)*1.5 centred on the face: convert
# image coords into that crop's coordinate system.
x1, y1, x2, y2 = 601.0, 298.0, 851.0, 578.0
w, h = x2 - x1, y2 - y1
side = max(w, h) * 1.5
cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
pts = (pts - np.array([cx - side / 2, cy - side / 2])) * (SIZE / side)

draw(list(range(0, 40)), 6, "idx_00_39.png")
draw(list(range(40, 75)), 6, "idx_40_74.png")
draw(list(range(75, 106)), 6, "idx_75_105.png")
