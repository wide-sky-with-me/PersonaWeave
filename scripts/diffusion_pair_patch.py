"""Non-production: derive a one-to-one pair runner from a REFace / DiffSwap++ ``inference_swap_selected.py``.

Run: python scripts/diffusion_pair_patch.py CHECKOUT_DIR [--safe-load SAFE_LOAD.py]
The upstream script crosses every source with every target and decodes all 50 intermediate predictions. Pairing
trial N's target with trial N's source is the only change we need, so the patch is a handful of exact string
replacements that fail loudly if upstream changes. ``torch.load`` is replaced by an allow-list loader before
anything else runs, because the checkpoints are pickles.
"""

import argparse
import shutil
from pathlib import Path

PROLOGUE = "import shutil, sys\nsys.path.insert(0, os.getcwd())\nimport safe_load\nsafe_load.install()\n"

REPLACEMENTS = [
    (
        "import argparse, os, sys, glob\n",
        "import argparse, os, sys, glob\n" + PROLOGUE,
    ),
    ("enumerate(os.listdir(target_path))", "enumerate(sorted(os.listdir(target_path)))"),
    ("enumerate(os.listdir(opt.src_folder))", "enumerate(sorted(os.listdir(opt.src_folder)))"),
    (
        "enumerate(os.listdir(source_cropped_path))",
        "enumerate(sorted(os.listdir(source_cropped_path), key=lambda n: int(n.split('.')[0])))",
    ),
    (
        "    del faceParsing_model\n",
        "    del faceParsing_model\n"
        "    assert len(os.listdir(target_cropped_path)) == len(os.listdir(target_path)), 'target alignment failed'\n"
        "    assert len(os.listdir(source_cropped_path)) == len(os.listdir(opt.src_folder)), 'source alignment failed'\n",
    ),
    (
        "test_dataset=VideoDataset(data_path=target_cropped_path,mask_path=target_mask_path,**test_args)",
        "pair_img = os.path.join(Base_path, 'pair_img'); pair_mask = os.path.join(Base_path, 'pair_mask')\n"
        "        for d in (pair_img, pair_mask):\n"
        "            shutil.rmtree(d, ignore_errors=True); os.makedirs(d)\n"
        "        shutil.copy(os.path.join(target_cropped_path, f'{i}.png'), os.path.join(pair_img, '0.png'))\n"
        "        shutil.copy(os.path.join(target_mask_path, f'{i}.png'), os.path.join(pair_mask, '0.png'))\n"
        "        test_dataset=VideoDataset(data_path=pair_img,mask_path=pair_mask,**test_args)",
    ),
    ("for i in range(len(other_pred_x_0)):", "for i in range(0):"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--safe-load", type=Path, required=True, help="Allow-list torch.load module to copy in")
    args = parser.parse_args()
    source = (args.checkout / "scripts/inference_swap_selected.py").read_text(encoding="utf-8")
    for old, new in REPLACEMENTS:
        if source.count(old) != 1:
            raise ValueError(f"Upstream script changed; expected exactly one occurrence of: {old[:60]!r}")
        source = source.replace(old, new)
    (args.checkout / "run_pairs.py").write_text(source, encoding="utf-8")
    shutil.copy(args.safe_load, args.checkout / "safe_load.py")
    print(f"Wrote {args.checkout / 'run_pairs.py'}")


if __name__ == "__main__":
    main()
