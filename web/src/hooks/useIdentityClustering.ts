import { useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../services/api';
import type { TargetImageItem } from '../types';

type TargetImagePatch = Partial<TargetImageItem>;
type BatchUpdate = { id: string; patch: TargetImagePatch };

interface UseIdentityClusteringOptions {
  targetImages: TargetImageItem[];
  updateTargetImageById: (id: string, patch: TargetImagePatch) => void;
  batchUpdateTargetImages: (updates: BatchUpdate[]) => void;
}

/** Detects identities for one image or clusters them across a target batch. */
export function useIdentityClustering({
  targetImages,
  updateTargetImageById,
  batchUpdateTargetImages,
}: UseIdentityClusteringOptions) {
  const [isClustering, setIsClustering] = useState(false);
  const [clusteringError, setClusteringError] = useState<string | null>(null);
  const completedFingerprintRef = useRef('');
  const generationRef = useRef(0);
  const batchFingerprint = useMemo(
    () => targetImages.map((target) => `${target.id}:${target.base64Data || target.url}`).join('|'),
    [targetImages],
  );

  useEffect(() => {
    const generation = ++generationRef.current;
    const controller = new AbortController();

    if (targetImages.length === 0) {
      completedFingerprintRef.current = '';
      setIsClustering(false);
      setClusteringError(null);
      return () => controller.abort();
    }
    if (completedFingerprintRef.current === batchFingerprint) return () => controller.abort();
    if (!targetImages.some((target) => target.faces === undefined)) {
      completedFingerprintRef.current = batchFingerprint;
      setClusteringError(null);
      return () => controller.abort();
    }

    const targetIds = targetImages.map((target) => target.id);
    const imageUrls = targetImages.map((target) => target.base64Data || target.url);

    const runClustering = async () => {
      setIsClustering(true);
      setClusteringError(null);
      try {
        if (imageUrls.length === 1) {
          const response = await api.detectFaces({ image_url: imageUrls[0] }, controller.signal);
          if (generationRef.current !== generation || controller.signal.aborted || !response) return;
          updateTargetImageById(targetIds[0], {
            faces: response.faces,
            imageWidth: response.image_width,
            imageHeight: response.image_height,
            facesDetected: response.faces.length,
          });
        } else {
          const response = await api.clusterFaces(
            { image_urls: imageUrls, similarity_threshold: 0.35 },
            controller.signal,
          );
          if (generationRef.current !== generation || controller.signal.aborted || !response?.images) return;
          batchUpdateTargetImages(response.images.flatMap((image: any) => {
            const id = targetIds[image.image_index];
            return id === undefined ? [] : [{
              id,
              patch: {
                faces: image.faces,
                imageWidth: image.image_width,
                imageHeight: image.image_height,
                facesDetected: image.faces.length,
              },
            }];
          }));
        }
        completedFingerprintRef.current = batchFingerprint;
      } catch (error) {
        if (generationRef.current !== generation || controller.signal.aborted) return;
        setClusteringError(error instanceof Error ? error.message : '身份聚类失败，请重试');
      } finally {
        if (generationRef.current === generation) setIsClustering(false);
      }
    };

    void runClustering();
    return () => controller.abort();
  }, [batchFingerprint, targetImages, updateTargetImageById, batchUpdateTargetImages]);
  return { isClustering, clusteringError };
}
