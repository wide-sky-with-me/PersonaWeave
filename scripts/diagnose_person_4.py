"""
Diagnostic script for Person #4 across all 5 graduation photos.
Investigates:
1. Detection and tracking similarity across image_0, image_2, image_4, image_6, image_9.
2. Pose (Yaw, Pitch, Roll), bounding box size, expression, lighting.
3. Target reference compatibility (Target 01, 02, 03 vs Person 4).
"""

import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.detector import FaceDetector
from core.face_metrics import calculate_yaw_ratio
from core.recognizer import FaceRecognizer


def main():
    detector = FaceDetector("models/buffalo_l/det_10g.onnx")
    recognizer = FaceRecognizer("models/buffalo_l/w600k_r50.onnx")

    # Load 5 test images
    img_files = sorted(
        [os.path.join("data/test_images", f) for f in os.listdir("data/test_images") if f.endswith(".png")]
    )
    print(f"Total images to diagnose: {len(img_files)}")

    # 1. Detect faces in image 0 and isolate Person 4 (index 3)
    img0 = cv2.imread(img_files[0])
    faces0 = detector.detect_faces(img0)
    faces0.sort(key=lambda f: f.bbox[0])

    p4_face0 = faces0[3]  # Person 4
    recognizer.extract_embedding(img0, p4_face0)
    p4_baseline_emb = p4_face0.embedding

    os.makedirs("outputs/person_4_diagnosis", exist_ok=True)

    print("\n" + "=" * 60)
    print("PERSON #4 TRACKING & SIMILARITY ACROSS ALL 5 PHOTOS:")
    print("=" * 60)

    for i, img_path in enumerate(img_files):
        fname = os.path.basename(img_path)
        img = cv2.imread(img_path)
        faces = detector.detect_faces(img)
        faces.sort(key=lambda f: f.bbox[0])

        print(f"\nImage [{i + 1}/5] {fname}:")
        print(f"  Detected {len(faces)} faces.")

        # Find best matching face to Person 4 baseline
        best_sim = -1.0
        best_face_idx = -1
        best_face = None

        for f_idx, face in enumerate(faces):
            recognizer.extract_embedding(img, face)
            sim = recognizer.compute_similarity(face.embedding, p4_baseline_emb)
            yaw = calculate_yaw_ratio(face.kps)
            w = face.bbox[2] - face.bbox[0]
            h = face.bbox[3] - face.bbox[1]
            print(
                f"    - Face #{f_idx + 1} (x={face.bbox[0]:.0f}, w={w:.0f}, h={h:.0f}): Similarity to P4 = {sim:.4f}, Yaw={yaw:.3f}"
            )
            if sim > best_sim:
                best_sim = sim
                best_face_idx = f_idx
                best_face = face

        # Save crop of the best candidate
        if best_face is not None:
            x1, y1, x2, y2 = map(int, best_face.bbox)
            pad = 40
            h_img, w_img = img.shape[:2]
            crop = img[max(0, y1 - pad) : min(h_img, y2 + pad), max(0, x1 - pad) : min(w_img, x2 + pad)]
            crop_out = f"outputs/person_4_diagnosis/img_{i}_person_4_sim_{best_sim:.2f}.jpg"
            cv2.imwrite(crop_out, crop)
            print(
                f"  --> Best match is Face #{best_face_idx + 1} with Similarity = {best_sim:.4f} (Saved to {crop_out})"
            )

    # 2. Check Target reference images vs Person 4
    ref_paths = ["data/test_target/01.png", "data/test_target/02.png", "data/test_target/03.png"]
    ref_imgs = [cv2.imread(p) for p in ref_paths]
    ref_faces = [detector.detect_faces(img, max_num=1, confidence_threshold=0.25)[0] for img in ref_imgs]
    for img, f in zip(ref_imgs, ref_faces, strict=False):
        recognizer.extract_embedding(img, f)

    print("\n" + "=" * 60)
    print("TARGET REFERENCE PHOTOS vs PERSON #4 CHARACTERISTICS:")
    print("=" * 60)
    for p, f in zip(ref_paths, ref_faces, strict=False):
        yaw = calculate_yaw_ratio(f.kps)
        w = f.bbox[2] - f.bbox[0]
        h = f.bbox[3] - f.bbox[1]
        print(f"  - {os.path.basename(p)}: BBox=({w:.0f}x{h:.0f}), Yaw={yaw:.3f}, KPS={f.kps.tolist()}")


if __name__ == "__main__":
    main()
