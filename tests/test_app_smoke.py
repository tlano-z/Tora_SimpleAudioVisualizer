from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from renderer import RenderSettings, render_static_preview


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AppSmokeTests(unittest.TestCase):
    def test_streamlit_app_starts(self) -> None:
        app = AppTest.from_file(PROJECT_ROOT / "app.py", default_timeout=30).run()

        self.assertEqual([], list(app.exception))
        self.assertEqual("Tora_SimpleAudioVisualizer", app.title[0].value)

    def test_static_preview_renders(self) -> None:
        settings = RenderSettings(
            width=320,
            height=180,
            bars=16,
            radius=40,
            bar_length=20,
            center_size=80,
            background_blur=2,
        )

        frame = render_static_preview(PROJECT_ROOT / "sample" / "image.png", settings)

        self.assertEqual((settings.height, settings.width, 3), frame.shape)


if __name__ == "__main__":
    unittest.main()
