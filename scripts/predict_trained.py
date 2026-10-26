import argparse
import json
from pathlib import Path

from coatdesk_ml.inference import TrainedRecognizer


def main():
    parser = argparse.ArgumentParser(description="Run explicitly selected offline models on one front photo")
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--front", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, default=Path(".local/predict-trained"))
    args = parser.parse_args()
    result = TrainedRecognizer(args.models).predict(args.front, args.scratch)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
