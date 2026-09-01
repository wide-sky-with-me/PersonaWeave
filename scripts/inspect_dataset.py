"""
Script to inspect dataset image properties:
- Image resolution
- Aspect ratio
- Color channels
"""

import glob
import os

import cv2


def inspect_folder(folder_path: str):
    print(f"=== Inspecting folder: {folder_path} ===")
    files = sorted(glob.glob(os.path.join(folder_path, "*.*")))
    for f in files:
        img = cv2.imread(f)
        if img is None:
            print(f"  [ERROR] Cannot read: {os.path.basename(f)}")
        else:
            h, w, c = img.shape
            size_kb = os.path.getsize(f) / 1024
            print(f"  - {os.path.basename(f):<40} : {w}x{h}, {c} channels, {size_kb:.1f} KB")


if __name__ == "__main__":
    inspect_folder("data/test_target")
    print()
    inspect_folder("data/test_images")
