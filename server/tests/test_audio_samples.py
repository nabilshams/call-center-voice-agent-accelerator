"""
Integration tests for audio transcription using sample audio files.
Tests the complete transcription pipeline with various audio formats.

Note: These tests use synthetic audio files for format validation.
For actual speech recognition accuracy testing, use real recorded speech samples.
"""

import gc
import os
import pytest
import shutil
import tempfile
import time
from pathlib import Path

from pydub import AudioSegment

# Path to audio samples
AUDIO_SAMPLES_DIR = Path(__file__).parent / "audio_samples"

# Check if ffmpeg is available
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
SKIP_FFMPEG_MSG = "ffmpeg not installed - required for non-WAV format tests"

# Check if audio samples exist
SAMPLES_EXIST = AUDIO_SAMPLES_DIR.exists() and any(AUDIO_SAMPLES_DIR.glob("*.wav"))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files with proper cleanup."""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    gc.collect()
    time.sleep(0.1)
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


@pytest.mark.skipif(not SAMPLES_EXIST, reason="Audio samples not generated. Run generate_test_audio.py first.")
class TestAudioSamplesExist:
    """Verify that all required audio sample files exist."""
    
    def test_single_speaker_samples_exist(self):
        """Test that single speaker samples exist in all formats."""
        formats = ['wav', 'mp3', 'ogg', 'flac', 'm4a']
        for fmt in formats:
            filepath = AUDIO_SAMPLES_DIR / f"sample_single_speaker.{fmt}"
            assert filepath.exists(), f"Missing: {filepath}"
    
    def test_multi_speaker_samples_exist(self):
        """Test that multi speaker samples exist in all formats."""
        formats = ['wav', 'mp3', 'ogg', 'flac', 'm4a']
        for fmt in formats:
            filepath = AUDIO_SAMPLES_DIR / f"sample_multi_speaker.{fmt}"
            assert filepath.exists(), f"Missing: {filepath}"
    
    def test_reference_transcriptions_exist(self):
        """Test that reference transcription files exist."""
        txt_files = [
            "sample_single_speaker.txt",
            "sample_multi_speaker.txt",
            "sample_short.txt",
            "sample_long.txt"
        ]
        for txt in txt_files:
            filepath = AUDIO_SAMPLES_DIR / txt
            assert filepath.exists(), f"Missing: {filepath}"


@pytest.mark.skipif(not SAMPLES_EXIST, reason="Audio samples not generated")
class TestWAVSampleLoading:
    """Test loading WAV sample files."""
    
    def test_load_single_speaker_wav(self):
        """Test loading single speaker WAV file."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        assert len(audio) > 0
        assert audio.frame_rate == 16000
        assert audio.channels == 1
        # TTS audio is ~10 seconds
        assert 8000 <= len(audio) <= 15000
    
    def test_load_multi_speaker_wav(self):
        """Test loading multi speaker WAV file."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        assert len(audio) > 0
        assert audio.frame_rate == 16000
        assert audio.channels == 1
        # TTS conversation is ~24 seconds
        assert 20000 <= len(audio) <= 30000
    
    def test_load_short_wav(self):
        """Test loading short duration WAV file."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_short.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        # TTS short greeting is ~2-3 seconds
        assert 2000 <= len(audio) <= 4000
    
    def test_load_long_wav(self):
        """Test loading long duration WAV file."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_long.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        # TTS long explanation is ~40-45 seconds
        assert 35000 <= len(audio) <= 50000


@pytest.mark.skipif(not SAMPLES_EXIST or not FFMPEG_AVAILABLE, reason="Audio samples or ffmpeg not available")
class TestMP3SampleLoading:
    """Test loading MP3 sample files."""
    
    def test_load_single_speaker_mp3(self):
        """Test loading single speaker MP3 file."""
        mp3_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.mp3"
        audio = AudioSegment.from_mp3(str(mp3_path))
        
        assert len(audio) > 0
        # TTS audio ~10 seconds (MP3 may have slightly different duration)
        assert 8000 <= len(audio) <= 15000
    
    def test_load_multi_speaker_mp3(self):
        """Test loading multi speaker MP3 file."""
        mp3_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.mp3"
        audio = AudioSegment.from_mp3(str(mp3_path))
        
        assert len(audio) > 0
        # TTS conversation ~24 seconds
        assert 20000 <= len(audio) <= 30000


@pytest.mark.skipif(not SAMPLES_EXIST or not FFMPEG_AVAILABLE, reason="Audio samples or ffmpeg not available")
class TestOGGSampleLoading:
    """Test loading OGG sample files."""
    
    def test_load_single_speaker_ogg(self):
        """Test loading single speaker OGG file."""
        ogg_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.ogg"
        audio = AudioSegment.from_ogg(str(ogg_path))
        
        assert len(audio) > 0
        # TTS audio ~10 seconds
        assert 8000 <= len(audio) <= 15000
    
    def test_load_multi_speaker_ogg(self):
        """Test loading multi speaker OGG file."""
        ogg_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.ogg"
        audio = AudioSegment.from_ogg(str(ogg_path))
        
        assert len(audio) > 0


@pytest.mark.skipif(not SAMPLES_EXIST or not FFMPEG_AVAILABLE, reason="Audio samples or ffmpeg not available")
class TestFLACSampleLoading:
    """Test loading FLAC sample files."""
    
    def test_load_single_speaker_flac(self):
        """Test loading single speaker FLAC file."""
        flac_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.flac"
        audio = AudioSegment.from_file(str(flac_path), format="flac")
        
        assert len(audio) > 0
        # TTS audio ~10 seconds (FLAC is lossless)
        assert 8000 <= len(audio) <= 15000
    
    def test_load_multi_speaker_flac(self):
        """Test loading multi speaker FLAC file."""
        flac_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.flac"
        audio = AudioSegment.from_file(str(flac_path), format="flac")
        
        assert len(audio) > 0


@pytest.mark.skipif(not SAMPLES_EXIST or not FFMPEG_AVAILABLE, reason="Audio samples or ffmpeg not available")
class TestM4ASampleLoading:
    """Test loading M4A sample files."""
    
    def test_load_single_speaker_m4a(self):
        """Test loading single speaker M4A file."""
        m4a_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.m4a"
        audio = AudioSegment.from_file(str(m4a_path), format="m4a")
        
        assert len(audio) > 0
        # TTS audio ~10 seconds
        assert 8000 <= len(audio) <= 15000
    
    def test_load_multi_speaker_m4a(self):
        """Test loading multi speaker M4A file."""
        m4a_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.m4a"
        audio = AudioSegment.from_file(str(m4a_path), format="m4a")
        
        assert len(audio) > 0


@pytest.mark.skipif(not SAMPLES_EXIST, reason="Audio samples not generated")
class TestReferenceTranscriptions:
    """Test loading and parsing reference transcription files."""
    
    def test_load_single_speaker_transcription(self):
        """Test loading single speaker reference transcription."""
        txt_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.txt"
        
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        assert len(content) > 0
        assert "[Speaker 1]" in content
    
    def test_load_multi_speaker_transcription(self):
        """Test loading multi speaker reference transcription."""
        txt_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.txt"
        
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        assert len(content) > 0
        assert "[Speaker 1]" in content
        assert "[Speaker 2]" in content
    
    def test_parse_speaker_labels(self):
        """Test parsing speaker labels from reference transcription."""
        txt_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.txt"
        
        with open(txt_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse speaker labels
        import re
        speaker_pattern = r'\[([^\]]+)\]'
        speakers = re.findall(speaker_pattern, content)
        
        assert len(speakers) >= 2
        assert "Speaker 1" in speakers
        assert "Speaker 2" in speakers


@pytest.mark.skipif(not SAMPLES_EXIST or not FFMPEG_AVAILABLE, reason="Audio samples or ffmpeg not available")
class TestFormatConversionWithSamples:
    """Test converting sample files between formats."""
    
    def test_mp3_to_wav_conversion(self, temp_dir):
        """Test converting sample MP3 to WAV."""
        mp3_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.mp3"
        wav_path = os.path.join(temp_dir, "converted.wav")
        
        audio = AudioSegment.from_mp3(str(mp3_path))
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        audio.export(wav_path, format='wav', parameters=["-acodec", "pcm_s16le"])
        
        # Verify conversion
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1
        assert converted.sample_width == 2
    
    def test_ogg_to_wav_conversion(self, temp_dir):
        """Test converting sample OGG to WAV."""
        ogg_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.ogg"
        wav_path = os.path.join(temp_dir, "converted.wav")
        
        audio = AudioSegment.from_ogg(str(ogg_path))
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        audio.export(wav_path, format='wav', parameters=["-acodec", "pcm_s16le"])
        
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1
    
    def test_flac_to_wav_conversion(self, temp_dir):
        """Test converting sample FLAC to WAV."""
        flac_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.flac"
        wav_path = os.path.join(temp_dir, "converted.wav")
        
        audio = AudioSegment.from_file(str(flac_path), format="flac")
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        audio.export(wav_path, format='wav', parameters=["-acodec", "pcm_s16le"])
        
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1
    
    def test_m4a_to_wav_conversion(self, temp_dir):
        """Test converting sample M4A to WAV."""
        m4a_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.m4a"
        wav_path = os.path.join(temp_dir, "converted.wav")
        
        audio = AudioSegment.from_file(str(m4a_path), format="m4a")
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        audio.export(wav_path, format='wav', parameters=["-acodec", "pcm_s16le"])
        
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1


@pytest.mark.skipif(not SAMPLES_EXIST, reason="Audio samples not generated")
class TestAudioDurationWithSamples:
    """Test audio duration calculation with sample files."""
    
    def test_short_sample_duration(self):
        """Test short sample is approximately 2-3 seconds."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_short.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        duration_seconds = len(audio) / 1000.0
        # TTS greeting: "Hello, how are you today?"
        assert 2.0 <= duration_seconds <= 4.0
    
    def test_single_speaker_duration(self):
        """Test single speaker sample is approximately 10 seconds."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_single_speaker.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        duration_seconds = len(audio) / 1000.0
        # TTS statement about transcription system
        assert 8.0 <= duration_seconds <= 15.0
    
    def test_multi_speaker_duration(self):
        """Test multi speaker sample is approximately 24 seconds."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_multi_speaker.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        duration_seconds = len(audio) / 1000.0
        # TTS conversation: customer support dialogue
        assert 20.0 <= duration_seconds <= 30.0
    
    def test_long_sample_duration(self):
        """Test long sample is approximately 42 seconds."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_long.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        duration_seconds = len(audio) / 1000.0
        # TTS explanation of transcription system
        assert 35.0 <= duration_seconds <= 50.0


@pytest.mark.skipif(not SAMPLES_EXIST, reason="Audio samples not generated")
class TestCostEstimationWithSamples:
    """Test cost estimation using actual sample durations."""
    
    COST_PER_MINUTE = 0.003  # $0.18/hour = $0.003/minute
    
    def test_short_sample_cost(self):
        """Test cost estimation for short sample (~1 second)."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_short.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        duration_minutes = len(audio) / 1000.0 / 60.0
        estimated_cost = duration_minutes * self.COST_PER_MINUTE
        
        # ~1 second = ~0.017 minutes = ~$0.00005
        assert estimated_cost < 0.001
    
    def test_long_sample_cost(self):
        """Test cost estimation for long sample (~15 seconds)."""
        wav_path = AUDIO_SAMPLES_DIR / "sample_long.wav"
        audio = AudioSegment.from_wav(str(wav_path))
        
        duration_minutes = len(audio) / 1000.0 / 60.0
        estimated_cost = duration_minutes * self.COST_PER_MINUTE
        
        # ~15 seconds = ~0.25 minutes = ~$0.00075
        assert estimated_cost < 0.01
    
    def test_one_hour_cost(self):
        """Test cost estimation for 1 hour of audio."""
        duration_minutes = 60
        estimated_cost = duration_minutes * self.COST_PER_MINUTE
        
        assert estimated_cost == pytest.approx(0.18, rel=1e-9)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
