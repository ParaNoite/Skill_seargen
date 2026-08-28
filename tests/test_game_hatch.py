import unittest
from pathlib import Path
from unittest.mock import patch

from skill_gather.game_hatch import GameHatchError, _game_file


class GameHatchTests(unittest.TestCase):
    def test_game_file_allows_only_configured_game_directories(self):
        with patch("skill_gather.game_hatch.Path.cwd", return_value=Path("C:/repo")):
            self.assertEqual(_game_file("baseline-2d/game.js").name, "game.js")
            self.assertEqual(_game_file("skill-3d/styles.css").name, "styles.css")
            with self.assertRaises(GameHatchError):
                _game_file("../AGENTS.md")


if __name__ == "__main__":
    unittest.main()
