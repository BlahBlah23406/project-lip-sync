"""Transcript parsing and clustering."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from captions import cluster_segments, extract_video_id


class TestExtractVideoId:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=WcMYaveKv1E",
        "https://youtu.be/WcMYaveKv1E",
        "https://www.youtube.com/embed/WcMYaveKv1E",
        "https://www.youtube.com/shorts/WcMYaveKv1E",
        "https://www.youtube.com/watch?v=WcMYaveKv1E&t=42s",
        "https://www.youtube.com/watch?list=PL123&v=WcMYaveKv1E",
    ])
    def test_accepts_every_youtube_url_shape(self, url):
        assert extract_video_id(url) == "WcMYaveKv1E"

    def test_ids_may_contain_dashes_and_underscores(self):
        assert extract_video_id("https://youtu.be/a-B_cD3fG_h") == "a-B_cD3fG_h"

    @pytest.mark.parametrize("url", [
        "https://example.com/watch?v=tooshort",
        "not a url at all",
        "",
    ])
    def test_rejects_what_it_cannot_parse(self, url):
        # Better to fail loudly than to dub the wrong video.
        with pytest.raises(ValueError):
            extract_video_id(url)


class TestClusterSegments:
    def test_empty_in_empty_out(self):
        assert cluster_segments([]) == []

    def test_merges_segments_separated_by_less_than_max_gap(self):
        segs = [
            {"text": "hello", "start": 0.0, "duration": 1.0},
            {"text": "world", "start": 1.2, "duration": 1.0},
        ]
        out = cluster_segments(segs, max_gap=0.4)
        assert len(out) == 1
        assert out[0]["text"] == "hello world"
        assert out[0]["start"] == 0.0
        assert out[0]["duration"] == pytest.approx(2.2)

    def test_keeps_segments_split_by_a_real_pause(self):
        segs = [
            {"text": "hello", "start": 0.0, "duration": 1.0},
            {"text": "world", "start": 5.0, "duration": 1.0},
        ]
        assert len(cluster_segments(segs, max_gap=0.4)) == 2

    def test_never_builds_a_cluster_past_max_duration(self):
        # Ten touching 1s segments would merge into 10s without the cap.
        segs = [{"text": f"s{i}", "start": float(i), "duration": 1.0} for i in range(10)]
        out = cluster_segments(segs, max_gap=0.4, max_duration=3.0)
        assert all(c["duration"] <= 3.0 for c in out)

    def test_does_not_mutate_its_input(self):
        segs = [
            {"text": "hello", "start": 0.0, "duration": 1.0},
            {"text": "world", "start": 1.1, "duration": 1.0},
        ]
        before = [dict(s) for s in segs]
        cluster_segments(segs)
        assert segs == before

    def test_every_word_survives_clustering(self):
        segs = [{"text": f"word{i}", "start": i * 0.5, "duration": 0.4} for i in range(8)]
        joined = " ".join(c["text"] for c in cluster_segments(segs))
        for i in range(8):
            assert f"word{i}" in joined
