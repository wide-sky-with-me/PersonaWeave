"""
Diagnostic script to test:
1. Target identity gallery extraction and pairwise consistency
2. Face detection count and bounding boxes on test_images
"""

import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.detector import FaceDetector
from core.recognizer import FaceRecognizer


def main():
    models_dir = "models"
    detector_path = os.path.join(models_dir, "buffalo_l", "det_10g.onnx")
    recognizer_path = os.path.join(models_dir, "buffalo_l", "w600k_r50.onnx")

    detector = FaceDetector(detector_path)
    recognizer = FaceRecognizer(recognizer_path)

    # 1. Target Identity Gallery
    print("=== Testing Target Identity Gallery (data/test_target) ===")
    target_files = sorted(glob.glob("data/test_target/*.png"))
    embs = []
    for f in target_files:
        img = cv2.imread(f)
        faces = detector.detect_faces(img)
        print(
            f"  {os.path.basename(f)}: Detected {len(faces)} face(s), score={faces[0].det_score:.3f}"
            if faces
            else f"  {os.path.basename(f)}: No face"
        )
        if faces:
            emb = recognizer.extract_embedding(img, faces[0])
            embs.append((os.path.basename(f), emb))

    # Pairwise similarity matrix
    print("\n--- Pairwise Similarity Matrix (Target Photos) ---")
    for name1, emb1 in embs:
        row = []
        for _, emb2 in embs:
            sim = recognizer.compute_similarity(emb1, emb2)
            row.append(f"{sim:.4f}")
        print(f"  {name1:<10}: {'  '.join(row)}")

    # 2. Test Images Face Detection
    print("\n=== Testing Test Images Face Detection (data/test_images) ===")
    test_files = sorted(glob.glob("data/test_images/*.png"))
    for f in test_files:
        img = cv2.imread(f)
        faces = detector.detect_faces(img)
        print(f"  {os.path.basename(f):<60}: Detected {len(faces)} face(s)")


if __name__ == "__main__":
    main()
