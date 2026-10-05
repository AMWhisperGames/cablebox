"""Lineup file round trip and duration parsing."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cablebox.library import Library, LibraryError, ProbeError, load_library, parse_duration_output, save_library
from cablebox.schedule import DAY_SECONDS, ORDER_SHUFFLE, Block, Channel, Video, now_playing


class LibraryTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        library = Library(
            channels=[
                Channel(
                    8,
                    "Anime",
                    (Block(20 * 3600, DAY_SECONDS, ("naruto", "anime"), ORDER_SHUFFLE),),
                )
            ],
            last_channel=8,
            guide_hours=2,
            videos=[Video(r"D:\Videos\a.mp4", "Naruto Ep 1", 5400.5, ("naruto", "anime"), "naruto", 12)],
            volume=35,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            save_library(path, library)
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            loaded = load_library(path)
        self.assertEqual(loaded.last_channel, 8)
        self.assertEqual(loaded.guide_hours, 2)
        self.assertEqual(loaded.videos[0].path, r"D:\Videos\a.mp4")
        self.assertEqual(loaded.videos[0].title, "Naruto Ep 1")
        self.assertAlmostEqual(loaded.videos[0].duration, 5400.5)
        self.assertEqual(loaded.videos[0].tags, ("naruto", "anime"))
        self.assertEqual(loaded.videos[0].series, "naruto")
        self.assertEqual(loaded.videos[0].episode, 12)
        self.assertEqual(loaded.volume, 35)
        block = loaded.channels[0].blocks[0]
        self.assertEqual(block.start, 20 * 3600)
        self.assertEqual(block.end, DAY_SECONDS)
        self.assertEqual(block.tags, ("naruto", "anime"))
        self.assertEqual(block.order, ORDER_SHUFFLE)

    def test_corrupt_file_is_not_replaced_by_a_silent_empty_library(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            path.write_text("{", encoding="utf-8")
            with self.assertRaises(LibraryError):
                load_library(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{")

    def test_partial_entries_are_skipped(self) -> None:
        payload = {
            "guide_hours": 99,
            "videos": [
                {"title": "no path"},
                {"path": "a.mp4", "tags": "Anime, Action", "duration": 10, "series": "nope", "episode": 4},
                {"path": "b.mp4", "duration": 0},
            ],
            "channels": [
                {"name": "no number"},
                {
                    "number": "4",
                    "name": "",
                    "blocks": [
                        {"start": "bad", "end": "22:00", "tags": ["anime"]},
                        {"start": 72000, "end": 79200, "tags": ["Anime", "action"], "order": "nope"},
                        {"start": "23:00", "end": "23:00", "tags": ["anime"]},
                    ],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_library(path)
        self.assertEqual(loaded.guide_hours, 6)
        self.assertEqual([video.path for video in loaded.videos], ["a.mp4", "b.mp4"])
        self.assertEqual(loaded.videos[0].tags, ("anime", "action"))
        self.assertEqual(loaded.videos[0].series, "nope")
        self.assertEqual(loaded.videos[0].episode, 4)
        self.assertEqual(loaded.videos[1].duration, 0)
        self.assertEqual(loaded.channels[0].name, "Channel 4")
        self.assertEqual(len(loaded.channels[0].blocks), 1)
        block = loaded.channels[0].blocks[0]
        self.assertEqual((block.start, block.end, block.order), (72000, 79200, "series"))
        self.assertEqual(block.tags, ("anime", "action"))

    def test_legacy_items_become_an_all_day_series(self) -> None:
        payload = {
            "channels": [
                {
                    "number": 7,
                    "name": "Old",
                    "items": [
                        {"path": "naruto.mp4", "title": "Naruto 01", "duration": 1200},
                        {"title": "nope"},
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_library(path)
        self.assertEqual(loaded.videos[0].path, "naruto.mp4")
        self.assertEqual(loaded.videos[0].title, "Naruto 01")
        self.assertEqual(loaded.videos[0].tags, ("imported-7",))
        block = loaded.channels[0].blocks[0]
        self.assertEqual(block.start, 0)
        self.assertEqual(block.end, DAY_SECONDS)
        self.assertEqual(block.tags, ("imported-7",))
        self.assertEqual(block.order, "series")

    def test_legacy_items_merge_onto_an_existing_video(self) -> None:
        payload = {
            "videos": [{"path": "naruto.mp4", "title": "Episode 1", "duration": 90, "tags": ["anime"], "series": 1, "episode": 5}],
            "channels": [
                {
                    "number": 7,
                    "name": "Old",
                    "items": [{"path": "naruto.mp4", "title": "Other", "duration": 5}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_library(path)
        self.assertEqual(len(loaded.videos), 1)
        video = loaded.videos[0]
        self.assertEqual(video.title, "Episode 1")
        self.assertEqual(video.duration, 90)
        self.assertEqual(video.tags, ("anime", "imported-7"))
        self.assertEqual(video.series, "1")
        self.assertEqual(video.episode, 5)

    def test_zero_duration_is_kept_and_does_not_air(self) -> None:
        library = Library(
            channels=[Channel(8, "Anime", (Block(0, DAY_SECONDS, ("naruto",)),))],
            videos=[
                Video("nope.mp4", "Nope", 0, ("naruto",)),
                Video("a.mp4", "Naruto Ep 1", 600, ("naruto",)),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "channels.json"
            save_library(path, library)
            loaded = load_library(path)
        self.assertEqual(loaded.videos[0].duration, 0)
        airing = now_playing(loaded.channels[0], datetime(2020, 1, 1, 0, 1, 0), loaded.videos)
        self.assertEqual(airing.title, "Naruto Ep 1")

    def test_parse_duration_uses_the_last_positive_number(self) -> None:
        text = "opening file\n0\n12.5\n"
        self.assertEqual(parse_duration_output(text), 12.5)

    def test_parse_duration_rejects_empty_output(self) -> None:
        with self.assertRaises(ProbeError):
            parse_duration_output("mpv: nothing to play\n")


if __name__ == "__main__":
    unittest.main()
