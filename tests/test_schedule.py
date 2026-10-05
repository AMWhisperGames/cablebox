"""Clock math for tagged channel blocks."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from cablebox.format import format_clock, format_day_offset, format_duration
from cablebox.schedule import (
    DAY_SECONDS,
    ORDER_SHUFFLE,
    Block,
    Channel,
    Video,
    find_channel,
    guide_blocks,
    now_playing,
    step_channel,
    videos_for_block,
)


def _video(path: str, title: str, duration: float, *tags: str) -> Video:
    return Video(path, title, duration, tags)


def _block(start_hour: int, end_hour: int, tags: tuple[str, ...], order: str = "series") -> Block:
    end = DAY_SECONDS if end_hour == 24 else end_hour * 3600
    return Block(start_hour * 3600, end, tags, order)


def _anime() -> tuple[Channel, tuple[Video, ...]]:
    videos = (
        _video("n1.mp4", "Naruto Ep 1", 3600, "naruto", "anime", "00s"),
        _video("n2.mp4", "Naruto Ep 2", 3600, "naruto", "anime", "00s"),
        _video("n10.mp4", "Naruto Ep 10", 3600, "naruto", "anime", "shounen"),
        _video("other.mp4", "Other", 3600, "anime", "00s"),
    )
    channel = Channel(
        8,
        "Anime",
        (
            _block(20, 22, ("naruto",)),
            _block(22, 24, ("00s", "anime"), ORDER_SHUFFLE),
        ),
    )
    return channel, videos


class TagMatchTests(unittest.TestCase):
    def test_block_requires_every_tag(self) -> None:
        videos = (
            _video("n1.mp4", "Naruto Ep 1", 3600, "naruto", "anime", "00s"),
            _video("n2.mp4", "Naruto Ep 2", 3600, "naruto", "anime", "00s"),
            _video("other.mp4", "Other", 3600, "anime", "00s"),
            _video("retro.mp4", "Retro", 3600, "00s"),
        )
        moment = datetime(2026, 10, 5, 20, 0, 0)
        both = videos_for_block(videos, _block(22, 24, ("00s", "anime")), moment, 8)
        self.assertEqual([video.path for video in both], ["n1.mp4", "n2.mp4", "other.mp4"])

        decade = videos_for_block(videos, _block(22, 24, ("00s",)), moment, 8)
        self.assertEqual([video.path for video in decade], ["n1.mp4", "n2.mp4", "other.mp4", "retro.mp4"])

        self.assertEqual(videos_for_block(videos, _block(22, 24, ("00s", "romance")), moment, 8), ())
        self.assertEqual(videos_for_block(videos, _block(22, 24, ()), moment, 8), ())

    def test_series_and_episode_numbers_set_the_order(self) -> None:
        videos = (
            _video("late.mp4", "aaa finale", 60, "naruto"),
            _video("early.mp4", "zzz premiere", 60, "naruto"),
            _video("next.mp4", "Shippuuden 1", 60, "naruto"),
            _video("extra.mp4", "Recap", 60, "naruto"),
        )
        numbered = (
            Video(videos[0].path, videos[0].title, videos[0].duration, videos[0].tags, "naruto", 10),
            Video(videos[1].path, videos[1].title, videos[1].duration, videos[1].tags, "naruto", 2),
            Video(videos[2].path, videos[2].title, videos[2].duration, videos[2].tags, "shippuden", 1),
            videos[3],
        )
        ordered = videos_for_block(numbered, _block(20, 22, ("naruto",)), datetime(2026, 10, 5, 20, 0), 8)
        self.assertEqual(
            [video.title for video in ordered],
            ["zzz premiere", "aaa finale", "Shippuuden 1", "Recap"],
        )
        seasons = (
            Video("a.mp4", "Late season", 60, ("show",), "Season 10", 1),
            Video("b.mp4", "Early season", 60, ("show",), "Season 2", 1),
        )
        ordered_seasons = videos_for_block(seasons, _block(20, 22, ("show",)), datetime(2026, 10, 5, 20, 0), 8)
        self.assertEqual([video.series for video in ordered_seasons], ["Season 2", "Season 10"])

    def test_natural_order_sorts_episode_numbers(self) -> None:
        videos = (
            _video("c.mp4", "Naruto Ep 10", 60, "naruto"),
            _video("a.mp4", "Naruto Ep 1", 60, "naruto"),
            _video("b.mp4", "Naruto Ep 2", 60, "naruto"),
        )
        ordered = videos_for_block(videos, _block(20, 22, ("naruto",)), datetime(2026, 10, 5, 20, 0), 8)
        self.assertEqual([video.title for video in ordered], ["Naruto Ep 1", "Naruto Ep 2", "Naruto Ep 10"])

    def test_zero_duration_does_not_air(self) -> None:
        videos = (
            _video("nope.mp4", "Nope", 0, "naruto"),
            _video("a.mp4", "Naruto Ep 1", 600, "naruto"),
        )
        moment = datetime(2020, 1, 1, 20, 1, 0)
        airing = now_playing(Channel(8, "Anime", (_block(20, 22, ("naruto",)),)), moment, videos)
        self.assertEqual(airing.title, "Naruto Ep 1")
        self.assertEqual(airing.offset, 60)


class SeriesTests(unittest.TestCase):
    def test_series_continues_the_next_day(self) -> None:
        video = _video("long.mp4", "Naruto Ep 1", 10000, "naruto")
        channel = Channel(8, "Anime", (_block(20, 22, ("naruto",)),))
        first_moment = datetime(2020, 1, 1, 20, 1, 40)
        first = now_playing(channel, first_moment, (video,))
        self.assertEqual(first.offset, 100)
        self.assertEqual(first.path, "long.mp4")

        nxt = now_playing(channel, first_moment + timedelta(days=1), (video,))
        self.assertEqual(nxt.offset, (first.offset + 2 * 3600) % video.duration)

    def test_consecutive_days_play_the_next_episode(self) -> None:
        videos = (
            _video("a.mp4", "Naruto Ep 1", 7200, "naruto"),
            _video("b.mp4", "Naruto Ep 2", 7200, "naruto"),
        )
        channel = Channel(8, "Anime", (_block(20, 22, ("naruto",)),))
        day = datetime(2020, 1, 1, 20, 0, 0)
        first = now_playing(channel, day, videos)
        second = now_playing(channel, day + timedelta(days=1), videos)
        self.assertEqual(first.title, "Naruto Ep 1")
        self.assertEqual(first.offset, 0)
        self.assertEqual(second.title, "Naruto Ep 2")
        self.assertEqual(second.offset, 0)

    def test_episode_cut_off_at_the_block_resumes_inside_the_file(self) -> None:
        videos = (
            _video("a.mp4", "Naruto Ep 1", 10800, "naruto"),
            _video("b.mp4", "Naruto Ep 2", 7200, "naruto"),
        )
        channel = Channel(8, "Anime", (_block(20, 22, ("naruto",)),))
        start = datetime(2020, 1, 1, 20, 0, 0)
        first = now_playing(channel, start, videos)
        self.assertEqual(first.title, "Naruto Ep 1")
        self.assertEqual(first.offset, 0)
        self.assertEqual(first.end, datetime(2020, 1, 1, 22, 0, 0))

        resumed = now_playing(channel, start + timedelta(days=1), videos)
        self.assertEqual(resumed.title, "Naruto Ep 1")
        self.assertEqual(resumed.offset, 7200)
        self.assertEqual(resumed.end, datetime(2020, 1, 2, 21, 0, 0))

        nxt = now_playing(channel, datetime(2020, 1, 2, 21, 0, 0), videos)
        self.assertEqual(nxt.title, "Naruto Ep 2")
        self.assertEqual(nxt.offset, 0)

    def test_mid_block_join(self) -> None:
        channel, videos = _anime()
        moment = datetime(2020, 1, 1, 20, 30, 0)
        airing = now_playing(channel, moment, videos)
        self.assertEqual(airing.title, "Naruto Ep 1")
        self.assertEqual(airing.offset, 1800)
        self.assertFalse(airing.is_standby)


class BlockWindowTests(unittest.TestCase):
    def test_gap_before_and_after_a_block(self) -> None:
        channel = Channel(8, "Anime", (_block(20, 22, ("naruto",)),))
        videos = (_video("a.mp4", "Naruto Ep 1", 3600, "naruto"),)
        before = now_playing(channel, datetime(2026, 10, 5, 19, 0, 0), videos)
        self.assertTrue(before.is_standby)
        self.assertEqual(before.title, "Please stand by")
        self.assertEqual(before.end, datetime(2026, 10, 5, 20, 0, 0))

        after = now_playing(channel, datetime(2026, 10, 5, 22, 10, 0), videos)
        self.assertTrue(after.is_standby)
        self.assertEqual(after.start, datetime(2026, 10, 5, 22, 0, 0))
        self.assertEqual(after.end, datetime(2026, 10, 6, 0, 0, 0))

    def test_gap_between_blocks_ends_at_the_next_block(self) -> None:
        channel = Channel(
            8,
            "Anime",
            (
                Block(20 * 3600, 22 * 3600, ("naruto",)),
                Block(22 * 3600 + 1800, 23 * 3600, ("00s",)),
            ),
        )
        videos = (_video("a.mp4", "Naruto Ep 1", 3600, "naruto", "00s"),)
        gap = now_playing(channel, datetime(2026, 10, 5, 22, 10, 0), videos)
        self.assertTrue(gap.is_standby)
        self.assertEqual(gap.start, datetime(2026, 10, 5, 22, 0, 0))
        self.assertEqual(gap.end, datetime(2026, 10, 5, 22, 30, 0))

        later = now_playing(channel, datetime(2026, 10, 5, 22, 0, 0), videos)
        self.assertTrue(later.is_standby)

    def test_adjacent_blocks_hand_off_on_the_later_block(self) -> None:
        channel = Channel(
            8,
            "Anime",
            (
                _block(20, 22, ("naruto",)),
                _block(22, 24, ("00s",)),
            ),
        )
        videos = (
            _video("n.mp4", "Naruto Ep 1", 3600, "naruto"),
            _video("o.mp4", "Other", 3600, "00s"),
        )
        airing = now_playing(channel, datetime(2020, 1, 1, 22, 0, 0), videos)
        self.assertEqual(airing.title, "Other")
        self.assertEqual(airing.offset, 0)

    def test_no_matching_videos_is_a_slate_until_the_block_ends(self) -> None:
        channel = Channel(8, "Anime", (_block(20, 22, ("naruto",)),))
        airing = now_playing(channel, datetime(2026, 10, 5, 21, 0, 0), ())
        self.assertTrue(airing.is_standby)
        self.assertEqual(airing.title, "No matching videos")
        self.assertIsNone(airing.path)
        self.assertEqual(airing.end, datetime(2026, 10, 5, 22, 0, 0))

    def test_empty_channel_stands_by_until_midnight(self) -> None:
        airing = now_playing(Channel(4, "Empty", ()), datetime(2026, 10, 5, 15, 0, 0), ())
        self.assertTrue(airing.is_standby)
        self.assertEqual(airing.title, "Please stand by")
        self.assertEqual(airing.end, datetime(2026, 10, 6, 0, 0, 0))


class ShuffleTests(unittest.TestCase):
    def test_shuffle_is_stable_through_the_day_and_joins_in_progress(self) -> None:
        videos = (
            _video("a.mp4", "Alpha", 1000, "00s", "anime"),
            _video("b.mp4", "Beta", 1000, "00s", "anime"),
            _video("c.mp4", "Gamma", 1000, "00s", "anime"),
        )
        block = _block(20, 22, ("00s", "anime"), ORDER_SHUFFLE)
        channel = Channel(8, "Anime", (block,))
        moment = datetime(2026, 10, 5, 20, 0, 0)
        first = videos_for_block(videos, block, moment, 8)
        again = videos_for_block(videos, block, moment.replace(hour=21), 8)
        self.assertEqual(first, again)

        start = now_playing(channel, moment, videos)
        later = now_playing(channel, moment + timedelta(seconds=100), videos)
        self.assertEqual(start.offset, 0)
        self.assertEqual(later.path, start.path)
        self.assertEqual(later.offset, 100)


class GuideTests(unittest.TestCase):
    def test_guide_covers_a_gap_and_a_show_without_holes(self) -> None:
        channel, videos = _anime()
        start = datetime(2026, 10, 5, 19, 30, 0)
        end = datetime(2026, 10, 5, 20, 30, 0)
        blocks = guide_blocks(channel, start, end, videos)
        self.assertGreaterEqual(len(blocks), 2)
        self.assertTrue(blocks[0].is_standby)
        self.assertFalse(blocks[1].is_standby)
        self.assertEqual(blocks[0].start, start)
        self.assertEqual(blocks[-1].end, end)
        for previous, current in zip(blocks, blocks[1:]):
            self.assertEqual(previous.end, current.start)
            self.assertGreater(current.end, current.start)

    def test_empty_window_returns_nothing(self) -> None:
        channel, videos = _anime()
        moment = datetime(2026, 10, 5, 12, 0, 0)
        self.assertEqual(guide_blocks(channel, moment, moment, videos), [])

    def test_empty_channel_guide_crosses_midnight(self) -> None:
        channel = Channel(4, "Empty", ())
        start = datetime(2026, 10, 5, 23, 0, 0)
        end = datetime(2026, 10, 6, 1, 0, 0)
        blocks = guide_blocks(channel, start, end, ())
        self.assertTrue(all(block.is_standby for block in blocks))
        self.assertEqual(blocks[0].start, start)
        self.assertEqual(blocks[-1].end, end)
        for previous, current in zip(blocks, blocks[1:]):
            self.assertEqual(previous.end, current.start)


class ChannelOrderTests(unittest.TestCase):
    def test_step_wraps_in_channel_number_order(self) -> None:
        channels = [
            Channel(11, "Eleven", ()),
            Channel(2, "Two", ()),
            Channel(5, "Five", ()),
        ]
        self.assertEqual(step_channel(channels, 2, 1).number, 5)
        self.assertEqual(step_channel(channels, 5, 1).number, 11)
        self.assertEqual(step_channel(channels, 11, 1).number, 2)
        self.assertEqual(step_channel(channels, 2, -1).number, 11)
        self.assertEqual(step_channel(channels, None, 1).number, 2)
        self.assertEqual(find_channel(channels, 5).name, "Five")
        self.assertIsNone(find_channel(channels, 99))
        self.assertIsNone(step_channel([], 1, 1))


class ClockFormatTests(unittest.TestCase):
    def test_half_hours_stay_on_the_minute(self) -> None:
        self.assertEqual(format_clock(datetime(2026, 10, 5, 14, 30, 0)), "2:30 PM")
        self.assertEqual(format_clock(datetime(2026, 10, 5, 0, 0, 0)), "12:00 AM")

    def test_editor_air_time_keeps_leftover_seconds(self) -> None:
        self.assertEqual(format_day_offset(0), "12:00 AM")
        self.assertEqual(format_day_offset(90), "12:01:30 AM")
        self.assertEqual(format_duration(90), "1m 30s")


if __name__ == "__main__":
    unittest.main()
