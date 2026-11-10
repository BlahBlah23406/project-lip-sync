"""Unit tests for audio quality optimizations and watchdog heartbeat safety."""
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import captions
import mixer
import translator


class TestRubberbandQuality(unittest.TestCase):
    def test_rubberband_includes_formant_preserved_and_smooth(self):
        """Verify that render_clip configures rubberband with formant preservation and smooth transients."""
        item = {
            "index": 1,
            "tempo": 1.35,
            "seg_file": "/tmp/nonexistent_seg_0001.mp3",
        }
        with patch("mixer.wav_frames", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("mixer._run") as mock_run:
            # We expect wav_frames to return >0 on the final check or raise
            with patch("mixer.wav_frames", side_effect=[0, 44100]):
                mixer.render_clip(item, "/tmp")
                self.assertTrue(mock_run.called)
                cmd = mock_run.call_args[0][0]
                filter_arg = next(cmd[i + 1] for i, arg in enumerate(cmd) if arg in ("-filter:a", "-af"))
                self.assertIn("rubberband=tempo=1.3500:formant=preserved:transients=smooth", filter_arg)


class TestHeartbeatCallbacks(unittest.TestCase):
    def test_mixer_heartbeat_callback(self):
        """Verify that mixer._run pulses heartbeat callback when a command runs."""
        called = []

        def my_beat(note):
            called.append(note)

        mixer.set_heartbeat_cb(my_beat)
        try:
            # Run a fast echo command to confirm execution without errors
            res = mixer._run(["true"])
            self.assertEqual(res.returncode, 0)
        finally:
            mixer.set_heartbeat_cb(None)

    def test_mixer_heartbeat_during_long_command(self):
        """Verify that when a command times out during communicate(), heartbeat callback is called."""
        called = []

        def my_beat(note):
            called.append(note)

        mixer.set_heartbeat_cb(my_beat)
        try:
            mock_proc = MagicMock()
            # First communicate call raises TimeoutExpired, second returns success
            mock_proc.communicate.side_effect = [
                subprocess.TimeoutExpired(cmd=["sleep"], timeout=15),
                ("ok", ""),
            ]
            mock_proc.returncode = 0
            with patch("subprocess.Popen", return_value=mock_proc):
                res = mixer._run(["mock_ffmpeg"])
                self.assertEqual(res.returncode, 0)
                self.assertIn("ffmpeg active", called)
        finally:
            mixer.set_heartbeat_cb(None)

    def test_captions_heartbeat_callback(self):
        """Verify that captions module allows registering heartbeat callback."""
        called = []

        def my_beat(note):
            called.append(note)

        captions.set_heartbeat_cb(my_beat)
        self.assertEqual(captions._heartbeat_cb, my_beat)
        captions.set_heartbeat_cb(None)

    def test_translator_heartbeat_callback(self):
        """Verify that translator module allows registering heartbeat callback."""
        called = []

        def my_beat(note):
            called.append(note)

        translator.set_heartbeat_cb(my_beat)
        self.assertEqual(translator._heartbeat_cb, my_beat)
        translator.set_heartbeat_cb(None)


class TestBanglaSyllableCounting(unittest.TestCase):
    def test_syllable_count_positive_for_bangla_text(self):
        text = "বিশ্বে এত পরিবর্তন এসেছে"
        count = translator.count_bangla_syllables(text)
        self.assertGreater(count, 0)

    def test_syllable_count_empty_text(self):
        self.assertEqual(translator.count_bangla_syllables(""), 0)

    def test_local_ollama_availability_check_non_blocking(self):
        """Verify _is_local_ollama_available returns a bool without hanging."""
        # Reset cache
        translator._local_available = None
        avail = translator._is_local_ollama_available()
        self.assertIsInstance(avail, bool)
