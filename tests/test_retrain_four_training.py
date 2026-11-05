"""Focused safeguards for the isolated four-model retraining experiment."""

import random
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch
from PIL import Image

from scripts.retrain_four_models import WORK, read
from scripts.retrain_four_training import KINDS, SIZES, Photos, build, change, checkpoint, training_checkpoint


class LightAugmentationTests(unittest.TestCase):
    def test_fixed_conditions_preserve_dimensions_and_original(self):
        pixels = np.random.default_rng(1).integers(0, 256, (96, 54, 3), dtype=np.uint8)
        image = Image.fromarray(pixels)
        for kind in KINDS:
            result = change(image, kind)
            self.assertEqual(result.size, image.size)
            self.assertTrue(np.array_equal(np.asarray(image), pixels))
            self.assertTrue(np.array_equal(np.asarray(change(image, kind, 0)), pixels))
        self.assertEqual(len(set(KINDS)), 9)

    def test_size_expansion_copies_outputs_by_name(self):
        from coatdesk_ab.data import SIZES as OLD_SIZES

        old, new = build("size", baseline=True), build("size")
        for i, label in enumerate(OLD_SIZES):
            j = SIZES.index(label)
            self.assertTrue(torch.equal(old.output[1].weight[i], new.output[1].weight[j]))
            self.assertTrue(torch.equal(old.output[1].bias[i], new.output[1].bias[j]))
        self.assertEqual(new.output[1].out_features, 18)
        self.assertNotIn("60", SIZES)

    @unittest.skipUnless((WORK / "frozen-dataset.json").exists(), "Local frozen experiment not present")
    def test_frozen_split_does_not_leak_photos_or_groups(self):
        rows = read(WORK / "frozen-dataset.json")["records"]
        for key in ("code", "sha256", "group"):
            partitions = {}
            for row in rows:
                self.assertEqual(partitions.setdefault(row[key], row["split"]), row["split"])
                if row["previous_fit"]:
                    self.assertEqual(row["split"], "train")
        raw70 = [r for r in rows if r["panel_eligible"] and r["raw_size"] == "70"]
        self.assertEqual([sum(r["split"] == s for r in raw70) for s in ("train", "dev", "test")], [5, 1, 1])
        for c in ("3598", "3599"):
            r = next(r for r in rows if r["code"] == c)
            self.assertEqual((r["raw_size"], r["size"]), ("160", "XL"))

    @unittest.skipUnless((WORK / "frozen-dataset.json").exists(), "Local frozen experiment not present")
    def test_resume_augmentation_is_independent_of_global_random(self):
        row = next(r for r in read(WORK / "frozen-dataset.json")["records"] if r["code"] == "3605")
        dataset = Photos([row], "size", epoch=3, train=True)
        first = dataset[0]
        random.seed(876)
        np.random.seed(123)
        torch.manual_seed(321)
        second = dataset[0]
        self.assertTrue(torch.equal(first[0], second[0]))
        self.assertTrue(torch.equal(first[1], second[1]))

    def test_checkpoint_resume_preserves_optimizer_and_all_random_states(self):
        random.seed(119)
        np.random.seed(119)
        torch.manual_seed(119)

        def make():
            model = torch.nn.Sequential(torch.nn.Linear(2, 4), torch.nn.Dropout(0.4), torch.nn.Linear(4, 2))
            optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, 4)
            return model, optimizer, scheduler

        def step(model, optimizer, scheduler):
            x = torch.rand(3, 2) + random.random() + float(np.random.random())
            y = torch.rand(3, 2)
            optimizer.zero_grad()
            torch.nn.functional.mse_loss(model(x), y).backward()
            optimizer.step()
            scheduler.step()

        model, optimizer, scheduler = make()
        step(model, optimizer, scheduler)
        with tempfile.TemporaryDirectory(dir=WORK) as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(WORK.resolve()))
            path = Path(directory) / "last.pt"
            checkpoint(
                path,
                training_checkpoint(model, optimizer, scheduler, 2, 12, [1.0, 1.0, 1.0], "size", "frozen-hash", [1, 0]),
            )
            step(model, optimizer, scheduler)
            step(model, optimizer, scheduler)
            expected = {k: v.clone() for k, v in model.state_dict().items()}
            expected_lr = scheduler.get_last_lr()
            saved = torch.load(path, map_location="cpu", weights_only=False)
            resumed, resumed_optimizer, resumed_scheduler = make()
            resumed.load_state_dict(saved["state_dict"])
            resumed_optimizer.load_state_dict(saved["optimizer"])
            resumed_scheduler.load_state_dict(saved["scheduler"])
            random.setstate(saved["random"])
            np.random.set_state(saved["numpy"])
            torch.set_rng_state(saved["torch"])
            if torch.cuda.is_available():
                torch.cuda.set_rng_state_all(saved["cuda"])
            step(resumed, resumed_optimizer, resumed_scheduler)
            step(resumed, resumed_optimizer, resumed_scheduler)
            for k, value in resumed.state_dict().items():
                self.assertTrue(torch.equal(value, expected[k]), k)
            self.assertEqual(resumed_scheduler.get_last_lr(), expected_lr)
            self.assertEqual((saved["epoch"], saved["cursor"], saved["order"]), (2, 12, [1, 0]))

    def test_failed_checkpoint_write_preserves_previous_complete_file(self):
        with tempfile.TemporaryDirectory(dir=WORK) as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(WORK.resolve()))
            path = Path(directory) / "last.pt"
            checkpoint(path, {"marker": torch.tensor([1])})

            def fail_after_partial_write(value, temporary):
                Path(temporary).write_bytes(b"incomplete")
                raise OSError("simulated interruption while saving")

            with patch("torch.save", side_effect=fail_after_partial_write):
                with self.assertRaises(OSError):
                    checkpoint(path, {"marker": torch.tensor([2])})
            self.assertEqual(torch.load(path, weights_only=True)["marker"].item(), 1)


if __name__ == "__main__":
    torch.set_num_threads(4)
    unittest.main()
