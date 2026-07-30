import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skill_gather.integrations.ffmpeg import FfmpegClient, FfmpegError


class FfmpegClientTests(unittest.TestCase):
    def test_extract_audio_runs_ffmpeg_to_wav(self):
        completed = Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "input.mp4"
            media.write_text("placeholder", encoding="utf-8")
            audio = Path(temp_dir) / "audio.wav"
            with patch(
                "skill_gather.integrations.ffmpeg.subprocess.run",
                return_value=completed,
            ) as run:
                result = FfmpegClient(binary="ffmpeg").extract_audio(media, audio)

        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["audio_path"], str(audio))
        args = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIn("-vn", args)
        self.assertEqual(args[-1], str(audio))
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_extract_frames_runs_ffmpeg_and_lists_frames(self):
        completed = Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "input.mp4"
            media.write_text("placeholder", encoding="utf-8")
            frames = Path(temp_dir) / "frames"
            frames.mkdir()
            (frames / "frame-000001.jpg").write_text("placeholder", encoding="utf-8")
            with patch(
                "skill_gather.integrations.ffmpeg.subprocess.run",
                return_value=completed,
            ) as run:
                result = FfmpegClient(binary="ffmpeg").extract_frames(
                    media,
                    frames,
                    interval_sec=10,
                )

        self.assertEqual(result["status"], "extracted")
        self.assertEqual(result["interval_sec"], 10)
        self.assertTrue(result["frame_paths"][0].endswith("frame-000001.jpg"))
        args = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIn("-vf", args)
        self.assertIn("fps=1/10", args)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_extract_audio_reports_sanitized_failure(self):
        completed = Mock(returncode=1, stdout="", stderr="bad https://example.test/tmp token=x")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("skill_gather.integrations.ffmpeg.subprocess.run", return_value=completed):
                with self.assertRaises(FfmpegError) as context:
                    FfmpegClient(binary="ffmpeg").extract_audio(
                        Path(temp_dir) / "input.mp4",
                        Path(temp_dir) / "audio.wav",
                    )

        self.assertEqual(context.exception.code, "audio_extract_failed")
        self.assertEqual(context.exception.returncode, 1)
        self.assertIn("[redacted-url]", context.exception.safe_summary)
        self.assertNotIn("token=x", context.exception.safe_summary)

    def test_extract_frames_rejects_non_positive_interval(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(FfmpegError) as context:
                FfmpegClient(binary="ffmpeg").extract_frames(
                    Path(temp_dir) / "input.mp4",
                    Path(temp_dir) / "frames",
                    interval_sec=0,
                )

        self.assertEqual(context.exception.code, "invalid_frame_interval")


if __name__ == "__main__":
    unittest.main()
