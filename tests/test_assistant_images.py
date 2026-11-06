"""Checks for the pasted-picture helpers: scaling, message parts, and prompts.

No AI service, clipboard, or window is involved.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ahamargin.assistant import (
    AssistantImage, MAX_IMAGES, MAX_IMAGE_EDGE, MAX_THUMB_EDGE, allow_read_tool,
    figure_name, figures_prompt, fit_size, image_content, validate_images,
)


class ImageHelperTests(unittest.TestCase):
    def test_fit_size_scales_the_long_edge_down_and_never_enlarges(self):
        self.assertEqual(fit_size(3000, 1500, MAX_IMAGE_EDGE), (1568, 784))
        self.assertEqual(fit_size(1500, 3000, MAX_IMAGE_EDGE), (784, 1568))
        self.assertEqual(fit_size(800, 600, MAX_IMAGE_EDGE), (800, 600))
        self.assertEqual(fit_size(1000, 500, MAX_THUMB_EDGE), (240, 120))
        self.assertEqual(fit_size(0, 0, MAX_IMAGE_EDGE), (0, 0))

    def test_at_most_four_pictures_travel_with_a_message(self):
        self.assertEqual(MAX_IMAGES, 4)

    def test_image_content_puts_the_text_first_and_every_picture_after_it(self):
        content = image_content("what is this?", [AssistantImage(b"AAA"), AssistantImage(b"BBB")])
        self.assertEqual(content, [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUFB"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QkJC"}},
        ])

    def test_a_picture_keeps_its_media_type(self):
        content = image_content("", [AssistantImage(b"AAA", "image/png")])
        self.assertEqual(content[-1]["image_url"]["url"], "data:image/png;base64,QUFB")

    def test_figure_names_are_numbered_in_the_order_they_were_pasted(self):
        self.assertEqual(figure_name(0), "figure-1.jpg")
        self.assertEqual(figure_name(3), "figure-4.jpg")

    def test_figures_prompt_points_the_agent_at_the_files_in_the_job_folder(self):
        prompt = figures_prompt([AssistantImage(b"AAA"), AssistantImage(b"BBB")])
        self.assertEqual(prompt,
            '<figure path="./figure-1.jpg">The user attached a picture. Read this file to see it.</figure>\n'
            '<figure path="./figure-2.jpg">The user attached a picture. Read this file to see it.</figure>')
        self.assertEqual(figures_prompt([]), "")

    def test_the_read_tool_is_only_given_to_a_turn_with_a_picture(self):
        plain = {"argv": ["claude", "--tools", "", "--model", "sonnet"], "stdin": True}
        with_picture = allow_read_tool(plain)
        self.assertEqual(with_picture["argv"][2], "Read")
        self.assertEqual(plain["argv"][2], "")

    def test_unsupported_and_oversized_pictures_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_images([AssistantImage(b"x", "image/gif")] * 1)
        with self.assertRaises(ValueError):
            validate_images([AssistantImage(b"x")] * (MAX_IMAGES + 1))
        with self.assertRaises(ValueError):
            validate_images([AssistantImage(b"")])
        validate_images([AssistantImage(b"x")] * MAX_IMAGES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
