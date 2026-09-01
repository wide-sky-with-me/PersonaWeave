"""
Face Recognition and Identity Matching module using ArcFace (w600k_r50 / Glint360k).
Supports multi-image identity gallery feature extraction, weighted aggregation,
and cosine similarity matching for targeted face swapping.
"""

import os

import numpy as np
from insightface.model_zoo import get_model

from core.detector import DetectedFace


class FaceRecognizer:
    """ArcFace 512-d Feature Extractor & Identity Gallery Matcher."""

    def __init__(self, model_path: str, providers: list[str] | None = None):
        """
        Initialize the ArcFace recognizer model.

        Args:
            model_path: Path to w600k_r50.onnx or glint360k ONNX model.
            providers: ONNX execution providers.
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Recognizer model not found at: {model_path}")

        if providers is None:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        self.model = get_model(model_path, providers=providers)
        self.model.prepare(ctx_id=0)

    def extract_embedding(self, image: np.ndarray, face: DetectedFace) -> np.ndarray:
        """
        Extracts and L2-normalizes the 512-dimensional ArcFace identity feature vector.

        Args:
            image: Full BGR image (H, W, 3).
            face: DetectedFace object with valid 5-landmarks `kps`.

        Returns:
            embedding: 1D numpy array of shape (512,) with unit L2 norm.
        """

        # InsightFace ArcFaceONNX expects an object with .kps or [bbox, kps]
        class DummyFace:
            def __init__(self, kps, bbox):
                self.kps = kps
                self.bbox = bbox

        dummy_face = DummyFace(face.kps, face.bbox)
        embedding = self.model.get(image, dummy_face)

        # Ensure L2 normalization
        norm = np.linalg.norm(embedding)
        if norm > 1e-6:
            embedding = embedding / norm
        face.embedding = embedding
        return embedding

    @staticmethod
    def build_gallery_embedding(embeddings: list[np.ndarray], weights: list[float] | None = None) -> np.ndarray:
        """
        Aggregates multiple face embeddings (e.g. from multi-angle reference photos)
        into a single unified, consistent identity vector using weighted normalized averaging.

        Formula:
            e_gallery = sum(w_i * v_i) / ||sum(w_i * v_i)||_2

        Args:
            embeddings: List of 512-d unit feature vectors.
            weights: Optional weights for each embedding.

        Returns:
            aggregated_embedding: 512-d normalized identity anchor vector.
        """
        if not embeddings:
            raise ValueError("No embeddings provided to build gallery.")

        if len(embeddings) == 1:
            emb = embeddings[0].astype(np.float32)
            return emb / max(np.linalg.norm(emb), 1e-6)

        if weights is None:
            weights = [1.0] * len(embeddings)

        weights = np.array(weights, dtype=np.float32)
        weights = weights / np.sum(weights)

        stacked = np.stack(embeddings, axis=0).astype(np.float32)
        weighted_sum = np.sum(stacked * weights[:, np.newaxis], axis=0)

        norm = np.linalg.norm(weighted_sum)
        if norm > 1e-6:
            weighted_sum = weighted_sum / norm
        return weighted_sum

    @staticmethod
    def compute_similarity(embedding_a: np.ndarray, embedding_b: np.ndarray) -> float:
        """
        Computes the cosine similarity between two unit L2 feature vectors.
        Since both are normalized, CosineSim = dot_product(a, b).
        """
        return float(np.dot(embedding_a.flatten(), embedding_b.flatten()))

    def filter_target_faces(
        self,
        image: np.ndarray,
        detected_faces: list[DetectedFace],
        target_embedding: np.ndarray,
        similarity_threshold: float = 0.55,
    ) -> list[DetectedFace]:
        """
        Extracts embeddings for all detected faces and filters those matching the target person.

        Args:
            image: Full BGR image.
            detected_faces: List of DetectedFace objects.
            target_embedding: 512-d target person anchor embedding.
            similarity_threshold: Minimum cosine similarity threshold (default 0.55).

        Returns:
            matched_faces: List of DetectedFace instances that match the target identity.
        """
        matched_faces: list[DetectedFace] = []
        for face in detected_faces:
            if face.embedding is None:
                self.extract_embedding(image, face)

            sim = self.compute_similarity(face.embedding, target_embedding)
            face.similarity = sim
            if sim >= similarity_threshold:
                matched_faces.append(face)

        return matched_faces
