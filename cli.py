"""
PersonaWeave Command-Line Interface (CLI).
Provides batch face swapping capabilities with:
1. Multi-reference target image gallery & dynamic pose-adaptive selection
2. Selective single-person swapping across multi-image batches (--person-index or --target-person)
3. High-fidelity restoration and seamless blending
"""

import argparse
import os
import sys
import time

import cv2
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.pipeline import PersonaWeavePipeline


def parse_args():
    parser = argparse.ArgumentParser(description="PersonaWeave: High-Fidelity Batch Targeted Face Swapper")
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        default="data/test_target",
        help="Path to source identity image or folder with reference photos (Gallery).",
    )
    parser.add_argument(
        "--person-index",
        "-p",
        type=int,
        default=None,
        choices=[1, 2, 3, 4, 5],
        help="Select which person to replace in the group photos (1 to 5, sorted left to right).",
    )
    parser.add_argument(
        "--target-person",
        "-tp",
        type=str,
        default=None,
        help="Optional reference photo of the target person to be replaced.",
    )
    parser.add_argument(
        "--source-mode",
        "-sm",
        type=str,
        default="best-match",
        choices=["best-match", "gallery"],
        help="How to utilize the 3 reference photos: 'best-match' (dynamic pose/lighting match) or 'gallery' (fused mean).",
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default="data/test_images",
        help="Path to input image or directory of images to process.",
    )
    parser.add_argument(
        "--output", "-o", type=str, default="outputs/results", help="Directory to save the processed output images."
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=0.38,
        help="Similarity threshold for target person matching (default 0.38).",
    )
    parser.add_argument("--models-dir", type=str, default="models", help="Directory containing ONNX models.")
    return parser.parse_args()


def collect_images(path: str) -> list[str]:
    """Collects all image files from a file path or directory."""
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    if os.path.isfile(path):
        return [path]
    elif os.path.isdir(path):
        files = []
        for root, _, filenames in os.walk(path):
            for fname in sorted(filenames):
                if os.path.splitext(fname)[1].lower() in valid_exts:
                    files.append(os.path.join(root, fname))
        return files
    return []


def main():
    args = parse_args()
    os.makedirs(args.output, exist_ok=True)

    print("==================================================")
    print("        PersonaWeave AI Batch Face Swapper        ")
    print("==================================================")

    # 1. Initialize Pipeline
    pipeline = PersonaWeavePipeline(models_dir=args.models_dir)

    # 2. Build Source Identity References & Gallery
    source_files = collect_images(args.source)
    if not source_files:
        print(f"[Error] No source images found at: {args.source}")
        sys.exit(1)

    print(f"\n[1/3] Loading {len(source_files)} Source Reference Photo(s) (Mode: {args.source_mode})...")
    source_references = pipeline.load_source_references(source_files)
    gallery_embedding = pipeline.recognizer.build_gallery_embedding([r["embedding"] for r in source_references])
    for r in source_references:
        print(f"  - {r['path']}")
    print("Source identity gallery loaded successfully.")

    # 3. Handle Target Person Filtering (Single Person Mode)
    input_files = collect_images(args.input)
    if not input_files:
        print(f"[Error] No target images found at: {args.input}")
        sys.exit(1)

    target_embedding = None
    if args.person_index is not None:
        # Automatically find the most frontal, highest-quality anchor face across candidate group images
        best_face_candidate = None
        best_quality_score = -1.0
        best_img = None
        best_img_name = ""

        for img_p in input_files:
            cur_img = cv2.imread(img_p)
            if cur_img is None:
                continue
            cur_faces = pipeline.detector.detect_faces(cur_img)
            if len(cur_faces) >= args.person_index:
                cur_faces.sort(key=lambda f: f.bbox[0])
                target_cand = cur_faces[args.person_index - 1]
                eye_dist = abs(target_cand.kps[1][0] - target_cand.kps[0][0])
                yaw_ratio = abs(target_cand.kps[2][0] - target_cand.kps[0][0]) / max(eye_dist, 1e-3)
                yaw_penalty = abs(yaw_ratio - 0.5)
                q_score = eye_dist * (1.0 - yaw_penalty)
                if q_score > best_quality_score:
                    best_quality_score = q_score
                    best_face_candidate = target_cand
                    best_img = cur_img
                    best_img_name = os.path.basename(img_p)

        if best_face_candidate is None:
            print(f"[Error] Could not find {args.person_index} faces in any input image.")
            sys.exit(1)

        target_embedding = pipeline.recognizer.extract_embedding(best_img, best_face_candidate)
        print("\n[2/3] Selective Single-Person Mode Active:")
        print(f"  -> Targeted Person: #{args.person_index} (from left to right in group photo)")
        print(f"  -> Identity Anchor Auto-Selected from: {best_img_name} (optimal frontal pose)")
        print(
            f"  -> Only Person #{args.person_index} will be swapped across all images. Other people remain untouched."
        )
    elif args.target_person:
        print(f"\n[2/3] Extracting target person reference from: {args.target_person}")
        target_embedding = pipeline.extract_target_reference_embedding(args.target_person)
        print("Target reference identity extracted.")
    else:
        print("\n[2/3] No target person specified -> Swapping all detected faces in target images.")

    # 4. Batch Process Target Images
    print(f"\n[3/3] Processing {len(input_files)} target image(s)...")
    total_detected = 0
    total_swapped = 0
    start_total_time = time.time()

    for img_path in tqdm(input_files, desc="Batch Processing"):
        img = cv2.imread(img_path)
        if img is None:
            print(f"\n[Warning] Failed to read {img_path}, skipping.")
            continue

        result = pipeline.process_image(
            image=img,
            source_references=source_references,
            gallery_embedding=gallery_embedding,
            target_embedding=target_embedding,
            similarity_threshold=args.threshold,
            source_mode=args.source_mode,
        )

        total_detected += result.faces_detected
        total_swapped += result.faces_swapped

        out_fname = f"swapped_{os.path.basename(img_path)}"
        out_path = os.path.join(args.output, out_fname)
        cv2.imwrite(out_path, result.output_image)

    total_time = time.time() - start_total_time
    avg_speed = total_time / max(len(input_files), 1)

    print("\n==================================================")
    print("               Processing Completed               ")
    print("==================================================")
    print(f"Total Images Processed : {len(input_files)}")
    print(f"Total Faces Detected   : {total_detected}")
    print(f"Total Faces Swapped    : {total_swapped}")
    print(f"Total Time Elapsed     : {total_time:.2f} s ({avg_speed:.2f} s/image)")
    print(f"Results Saved To       : {os.path.abspath(args.output)}")
    print("==================================================")


if __name__ == "__main__":
    main()
