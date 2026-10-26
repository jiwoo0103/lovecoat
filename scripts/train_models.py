"""Reproducible offline Tesseract fine tuning and head-placard classification."""
import argparse
from pathlib import Path

from coatdesk_ml.training import evaluate, make_lstmf, prepare, start_ocr_training, train_shape


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "lstmf", "ocr", "shape", "evaluate"])
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--name", default="ocr-inner")
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--ocr-dir", type=Path)
    parser.add_argument("--role", default="outer_validation", choices=["inner_dev", "outer_validation"])
    args = parser.parse_args()
    work = args.work.resolve(strict=True)
    if args.phase == "prepare":
        prepare(args.dataset, work)
    elif args.phase == "lstmf":
        make_lstmf(work)
    elif args.phase == "ocr":
        start_ocr_training(work, args.name, args.iterations, args.final)
    elif args.phase == "shape":
        train_shape(work)
    else:
        evaluate(work, args.ocr_dir.resolve(strict=True), args.role, args.name)


if __name__ == "__main__":
    main()
