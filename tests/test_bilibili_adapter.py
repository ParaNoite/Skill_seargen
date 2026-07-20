import unittest

from skill_gather.adapters.bilibili import manifest_from_yt_dlp_metadata
from skill_gather.source import infer_source


class BilibiliAdapterTests(unittest.TestCase):
    def test_manifest_from_yt_dlp_metadata_maps_basic_fields(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD/"
        source = infer_source(url)

        manifest = manifest_from_yt_dlp_metadata(
            url,
            source,
            {
                "title": "Skill Demo",
                "uploader": "Teacher",
                "duration": 3661,
                "subtitles": {"zh-CN": [{"url": "https://example.test/subtitle.json"}]},
            },
        )

        self.assertEqual(manifest.title, "Skill Demo")
        self.assertEqual(manifest.author, "Teacher")
        self.assertEqual(manifest.duration_sec, 3661)
        self.assertTrue(manifest.subtitle_available)
        self.assertEqual(manifest.risk_flags, [])

    def test_manifest_from_yt_dlp_metadata_marks_no_subtitle(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD/"
        source = infer_source(url)

        manifest = manifest_from_yt_dlp_metadata(
            url,
            source,
            {
                "title": "No Subtitle Demo",
                "channel": "Teacher",
                "duration": None,
            },
        )

        self.assertEqual(manifest.author, "Teacher")
        self.assertEqual(manifest.duration_sec, 0)
        self.assertFalse(manifest.subtitle_available)
        self.assertIn("no_subtitle", manifest.risk_flags)


if __name__ == "__main__":
    unittest.main()
