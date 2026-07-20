import unittest

from skill_gather.source import SourceInferenceError, infer_source


class SourceInferenceTests(unittest.TestCase):
    def test_infers_bilibili_bv_url(self):
        info = infer_source("https://www.bilibili.com/video/BV1xx411c7mD/")

        self.assertEqual(info.source, "bilibili")
        self.assertEqual(info.source_id, "BV1xx411c7mD")

    def test_rejects_unknown_source(self):
        with self.assertRaises(SourceInferenceError):
            infer_source("https://example.com/watch?v=1")


if __name__ == "__main__":
    unittest.main()
