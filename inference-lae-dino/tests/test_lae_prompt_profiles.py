from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from detection_policy import parent_class_for_label
from lae_vocabulary import COCO_CLASSES, LAE_80C_CLASSES, chunk_classes, prompt_from_classes, split_prompt_tokens
from main import _to_grounding_dino_prompt, resolve_prompt_plan


class LaePromptProfileTests(unittest.TestCase):
    def test_official_lae80c_has_80_classes(self) -> None:
        self.assertEqual(len(LAE_80C_CLASSES), 80)
        self.assertEqual(len(set(LAE_80C_CLASSES)), 80)

    def test_prompt_uses_official_custom_entity_style(self) -> None:
        prompt = prompt_from_classes(LAE_80C_CLASSES[:5])
        self.assertEqual(
            prompt,
            "airplane . airport . groundtrackfield . harbor . baseballfield",
        )
        self.assertNotIn(",", prompt)
        self.assertNotIn("military_vehicle", prompt)
        self.assertNotIn("storage_tank", prompt)

    def test_chunking_covers_each_class_once(self) -> None:
        chunks = chunk_classes(LAE_80C_CLASSES, 20)
        flattened = [item for chunk in chunks for item in chunk]
        self.assertEqual(len(chunks), 4)
        self.assertEqual(flattened, list(LAE_80C_CLASSES))

    def test_coco_profile_uses_coco_vocabulary(self) -> None:
        plan = resolve_prompt_plan({"prompt_profile": "coco"})
        self.assertEqual(plan["profile"], "coco")
        self.assertEqual(plan["classes"], list(COCO_CLASSES))
        self.assertEqual(len(COCO_CLASSES), 80)

    def test_split_prompt_round_trips_chunk(self) -> None:
        classes = LAE_80C_CLASSES[20:40]
        self.assertEqual(split_prompt_tokens(prompt_from_classes(classes)), list(classes))

    def test_grounding_dino_prompt_format(self) -> None:
        prompt = "Dry Cargo Ship . Fixed-wing Aircraft . Bus"
        self.assertEqual(
            _to_grounding_dino_prompt(prompt),
            "dry cargo ship. fixed-wing aircraft. bus.",
        )

    def test_official_labels_map_to_audit_parent_classes(self) -> None:
        cases = {
            "Dry Cargo Ship": "ship",
            "Fixed-wing Aircraft": "aircraft",
            "Cargo Truck": "vehicle",
            "Tower crane": "infrastructure",
            "baseballfield": "recreation",
            "Damaged Building": "building",
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertEqual(parent_class_for_label(label), expected)


if __name__ == "__main__":
    unittest.main()
