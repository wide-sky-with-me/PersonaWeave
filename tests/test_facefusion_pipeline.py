from types import SimpleNamespace

import numpy as np

from core.face_metrics import has_visible_face_change
from core.pipeline import PersonaWeavePipeline
from core.swapper import create_facefusion_box_mask, explode_pixel_boost, implode_pixel_boost


def test_facefusion_box_mask_has_soft_protected_border():
    mask = create_facefusion_box_mask((256, 256), blur=0.3)

    assert mask.shape == (256, 256)
    assert mask.dtype == np.float32
    assert mask[0, 0] < 0.05
    assert mask[128, 128] > 0.99
    assert 0.0 < mask[20, 128] < 1.0


def test_pixel_boost_round_trip_preserves_aligned_crop_exactly():
    crop = np.arange(256 * 256 * 3, dtype=np.uint32).reshape(256, 256, 3) % 256
    crop = crop.astype(np.uint8)

    tiles = implode_pixel_boost(crop, 2, (128, 128))
    restored = explode_pixel_boost(tiles, 2, (128, 128))

    assert len(tiles) == 4
    assert all(tile.shape == (128, 128, 3) for tile in tiles)
    assert np.array_equal(restored, crop)


def test_large_inswapper_face_caps_pixel_boost_at_256_working_crop():
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.parser = None
    pipeline.occluder = None
    pipeline.region_parser = None
    canvas = np.full((800, 800, 3), 127, dtype=np.uint8)
    target = SimpleNamespace(
        bbox=np.asarray([200.0, 180.0, 600.0, 650.0]),
        kps=np.asarray([[310, 330], [490, 330], [400, 420], [335, 510], [465, 510]], dtype=np.float32),
    )

    class RecordingSwapper:
        spec = SimpleNamespace(name="inswapper_128", input_size=(128, 128), crop_template="inswapper_128")

        def __init__(self):
            self.calls = []

        def infer_aligned(self, crop, _embedding):
            self.calls.append(crop.shape)
            return crop

    swapper = RecordingSwapper()
    result = pipeline.swap_single_face_facefusion(
        canvas,
        target,
        np.ones(512, dtype=np.float32),
        swapper=swapper,
        enable_dense_mask=False,
        manual_mask=None,
        enable_color_transfer=False,
    )

    # A 128px model uses four interleaved tiles for one 256px crop, not sixteen
    # tiles for a 512px crop; the latter caused visible texture loss on large faces.
    assert swapper.calls == [(128, 128, 3)] * 4
    assert result.shape == canvas.shape


def test_pixel_boosted_inswapper_face_keeps_visible_identity_strength():
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.parser = None
    pipeline.occluder = None
    pipeline.region_parser = None
    canvas = np.full((400, 400, 3), 100, dtype=np.uint8)
    target = SimpleNamespace(
        bbox=np.asarray([130.0, 120.0, 250.0, 260.0]),
        kps=np.asarray([[160, 170], [220, 170], [190, 205], [168, 235], [212, 235]], dtype=np.float32),
    )

    class FlatSwapper:
        spec = SimpleNamespace(name="inswapper_128", input_size=(128, 128), crop_template="inswapper_128")

        def infer_aligned(self, crop, _embedding):
            return np.full_like(crop, 200)

    result = pipeline.swap_single_face_facefusion(
        canvas,
        target,
        np.ones(512, dtype=np.float32),
        swapper=FlatSwapper(),
        enable_dense_mask=False,
        manual_mask=None,
        enable_color_transfer=False,
    )

    # This 140px face uses a 256px working crop, so the result must retain enough
    # generated identity to be visibly different from the 100-valued target.
    assert 168 <= int(result[205, 190, 0]) <= 176


def test_inswapper_identity_blend_strength_increases_generated_face_visibility():
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.parser = None
    pipeline.occluder = None
    pipeline.region_parser = None
    canvas = np.full((400, 400, 3), 100, dtype=np.uint8)
    target = SimpleNamespace(
        bbox=np.asarray([130.0, 120.0, 250.0, 260.0]),
        kps=np.asarray([[160, 170], [220, 170], [190, 205], [168, 235], [212, 235]], dtype=np.float32),
    )

    class FlatSwapper:
        spec = SimpleNamespace(name="inswapper_128", input_size=(128, 128), crop_template="inswapper_128")

        @staticmethod
        def infer_aligned(crop, _embedding):
            return np.full_like(crop, 200)

    default_result = pipeline.swap_single_face_facefusion(
        canvas,
        target,
        np.ones(512, dtype=np.float32),
        swapper=FlatSwapper(),
        enable_dense_mask=False,
        manual_mask=None,
        enable_color_transfer=False,
    )
    stronger_result = pipeline.swap_single_face_facefusion(
        canvas,
        target,
        np.ones(512, dtype=np.float32),
        swapper=FlatSwapper(),
        enable_dense_mask=False,
        manual_mask=None,
        identity_blend_strength=0.88,
        enable_color_transfer=False,
    )

    assert int(stronger_result[205, 190, 0]) > int(default_result[205, 190, 0])


def test_hyperswap_learned_mask_limits_profile_paste_region():
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.parser = None
    pipeline.occluder = None
    pipeline.region_parser = None
    canvas = np.full((400, 400, 3), 100, dtype=np.uint8)
    target = SimpleNamespace(
        bbox=np.asarray([130.0, 120.0, 250.0, 260.0]),
        kps=np.asarray([[160, 170], [220, 170], [190, 205], [168, 235], [212, 235]], dtype=np.float32),
    )

    class MaskedSwapper:
        spec = SimpleNamespace(name="hyperswap_1c_256", input_size=(256, 256), crop_template="arcface_128")

        @staticmethod
        def infer_aligned(crop, _embedding):
            return np.full_like(crop, 200), np.zeros((256, 256), dtype=np.float32)

    result = pipeline.swap_single_face_facefusion(
        canvas,
        target,
        np.ones(512, dtype=np.float32),
        swapper=MaskedSwapper(),
        enable_dense_mask=False,
        manual_mask=None,
        enable_color_transfer=False,
    )

    assert np.array_equal(result, canvas)


def test_multi_mapping_reports_the_stable_person_id_that_was_actually_swapped():
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.swapper_model = "inswapper_128"
    face = SimpleNamespace(
        bbox=np.asarray([20.0, 20.0, 80.0, 80.0]),
        kps=np.asarray([[35, 40], [65, 40], [50, 52], [38, 65], [62, 65]], dtype=np.float32),
        embedding=None,
    )
    pipeline.detector = SimpleNamespace(detect_faces=lambda _image: [face])

    def extract_embedding(_image, detected_face):
        detected_face.embedding = np.ones(2, dtype=np.float32)

    pipeline.recognizer = SimpleNamespace(
        extract_embedding=extract_embedding,
        compute_similarity=lambda _left, _right: 1.0,
    )
    pipeline.select_best_reference_embedding = lambda **_kwargs: np.ones(2, dtype=np.float32)
    pipeline.route_swapper = lambda *_args: SimpleNamespace(
        requested="auto", selected="inswapper_128", reason="test", fallback=False
    )
    pipeline.get_swapper = lambda _name: object()
    pipeline.swap_single_face_facefusion = lambda **kwargs: kwargs["canvas"] + 10

    result = pipeline.process_image_multi_mapping(
        np.zeros((100, 100, 3), dtype=np.uint8),
        mappings=[
            {
                "target_index": 42,
                "target_embedding": np.ones(2, dtype=np.float32),
                "gallery_embedding": np.ones(2, dtype=np.float32),
                "source_references": [],
                "source_mode": "gallery",
                "similarity_threshold": 0.35,
            }
        ],
    )

    assert result.faces_swapped == 1
    assert result.swapped_person_indices == [42]
    assert result.person_outcomes == [
        {
            "person_index": 42,
            "status": "SWAPPED",
            "reason": "visible_change_confirmed",
            "model": "inswapper_128",
        }
    ]


def test_multi_mapping_skips_extreme_profile_without_matching_source_pose():
    pipeline = object.__new__(PersonaWeavePipeline)
    pipeline.swapper_model = "hyperswap_1c_256"
    face = SimpleNamespace(
        bbox=np.asarray([20.0, 20.0, 80.0, 80.0]),
        kps=np.asarray([[30, 40], [70, 40], [70, 52], [38, 65], [62, 65]], dtype=np.float32),
        embedding=None,
    )
    pipeline.detector = SimpleNamespace(detect_faces=lambda _image: [face])

    def extract_embedding(_image, detected_face):
        detected_face.embedding = np.ones(2, dtype=np.float32)

    pipeline.recognizer = SimpleNamespace(
        extract_embedding=extract_embedding,
        compute_similarity=lambda _left, _right: 1.0,
    )
    pipeline.swap_single_face_facefusion = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("inference must not run")
    )
    result = pipeline.process_image_multi_mapping(
        np.zeros((100, 100, 3), dtype=np.uint8),
        mappings=[
            {
                "target_index": 1,
                "target_embedding": np.ones(2, dtype=np.float32),
                "gallery_embedding": np.ones(2, dtype=np.float32),
                "source_references": [{"yaw": 0.0, "embedding": np.ones(2, dtype=np.float32)}],
                "similarity_threshold": 0.35,
            }
        ],
    )

    assert result.faces_swapped == 0
    assert result.requested_person_indices == [1]
    assert result.person_outcomes[0]["reason"] == "extreme_pose_without_matching_reference"


def test_visible_face_change_rejects_no_op_and_tiny_noise():
    before = np.full((100, 100, 3), 100, dtype=np.uint8)
    bbox = np.asarray([20.0, 20.0, 80.0, 80.0])
    unchanged = before.copy()
    noisy = before.copy()
    noisy[30:70, 30:70] = 102
    changed = before.copy()
    changed[20:80, 20:80] = 110

    assert not has_visible_face_change(before, unchanged, bbox)
    assert not has_visible_face_change(before, noisy, bbox)
    assert has_visible_face_change(before, changed, bbox)
