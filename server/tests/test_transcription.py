"""
Test cases for audio transcription functionality.
Tests supported formats: WAV, MP3, OGG, FLAC, M4A
"""

import gc
import io
import os
import pytest
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Test audio file generation using pydub
from pydub import AudioSegment
from pydub.generators import Sine

# Check if ffmpeg is available
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
SKIP_FFMPEG_MSG = "ffmpeg not installed - required for non-WAV format tests"


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files with proper cleanup on Windows."""
    tmpdir = tempfile.mkdtemp()
    yield tmpdir
    # Force garbage collection to release file handles
    gc.collect()
    time.sleep(0.1)  # Small delay for Windows to release handles
    # Try to clean up, ignore errors on Windows
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


class TestAudioFormatSupport:
    """Test cases for supported audio format conversion."""
    
    @pytest.fixture
    def sample_audio(self):
        """Generate a simple test audio segment (1 second of 440Hz sine wave)."""
        # Generate 1 second of 440Hz sine wave
        audio = Sine(440).to_audio_segment(duration=1000)
        audio = audio.set_frame_rate(16000).set_channels(1)
        return audio
    
    def test_wav_format_creation(self, sample_audio, temp_dir):
        """Test WAV format file creation and properties."""
        wav_path = os.path.join(temp_dir, "test.wav")
        sample_audio.export(wav_path, format="wav")
        
        assert os.path.exists(wav_path)
        assert os.path.getsize(wav_path) > 0
        
        # Verify we can load it back
        loaded = AudioSegment.from_wav(wav_path)
        assert len(loaded) == 1000  # 1 second = 1000ms
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_mp3_format_creation(self, sample_audio, temp_dir):
        """Test MP3 format file creation and properties."""
        mp3_path = os.path.join(temp_dir, "test.mp3")
        sample_audio.export(mp3_path, format="mp3")
        
        assert os.path.exists(mp3_path)
        assert os.path.getsize(mp3_path) > 0
        
        # Verify we can load it back
        loaded = AudioSegment.from_mp3(mp3_path)
        # MP3 encoding may slightly change duration
        assert 900 <= len(loaded) <= 1100
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_ogg_format_creation(self, sample_audio, temp_dir):
        """Test OGG format file creation and properties."""
        ogg_path = os.path.join(temp_dir, "test.ogg")
        sample_audio.export(ogg_path, format="ogg")
        
        assert os.path.exists(ogg_path)
        assert os.path.getsize(ogg_path) > 0
        
        # Verify we can load it back
        loaded = AudioSegment.from_ogg(ogg_path)
        assert 900 <= len(loaded) <= 1100
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_flac_format_creation(self, sample_audio, temp_dir):
        """Test FLAC format file creation and properties."""
        flac_path = os.path.join(temp_dir, "test.flac")
        sample_audio.export(flac_path, format="flac")
        
        assert os.path.exists(flac_path)
        assert os.path.getsize(flac_path) > 0
        
        # Verify we can load it back
        loaded = AudioSegment.from_file(flac_path, format="flac")
        assert len(loaded) == 1000
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_m4a_format_creation(self, sample_audio, temp_dir):
        """Test M4A format file creation and properties."""
        m4a_path = os.path.join(temp_dir, "test.m4a")
        sample_audio.export(m4a_path, format="ipod")  # ipod format for m4a
        
        assert os.path.exists(m4a_path)
        assert os.path.getsize(m4a_path) > 0
        
        # Verify we can load it back
        loaded = AudioSegment.from_file(m4a_path, format="m4a")
        assert 900 <= len(loaded) <= 1100


class TestAudioConversion:
    """Test cases for audio format conversion to WAV."""
    
    @pytest.fixture
    def sample_audio(self):
        """Generate a simple test audio segment."""
        audio = Sine(440).to_audio_segment(duration=2000)  # 2 seconds
        return audio
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_mp3_to_wav_conversion(self, sample_audio, temp_dir):
        """Test converting MP3 to WAV format."""
        mp3_path = os.path.join(temp_dir, "input.mp3")
        wav_path = os.path.join(temp_dir, "output.wav")
        
        # Create MP3 file
        sample_audio.export(mp3_path, format="mp3")
        
        # Convert to WAV
        audio = AudioSegment.from_mp3(mp3_path)
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_path, format="wav")
        
        # Verify conversion
        assert os.path.exists(wav_path)
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_ogg_to_wav_conversion(self, sample_audio, temp_dir):
        """Test converting OGG to WAV format."""
        ogg_path = os.path.join(temp_dir, "input.ogg")
        wav_path = os.path.join(temp_dir, "output.wav")
        
        # Create OGG file
        sample_audio.export(ogg_path, format="ogg")
        
        # Convert to WAV
        audio = AudioSegment.from_ogg(ogg_path)
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_path, format="wav")
        
        # Verify conversion
        assert os.path.exists(wav_path)
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_flac_to_wav_conversion(self, sample_audio, temp_dir):
        """Test converting FLAC to WAV format."""
        flac_path = os.path.join(temp_dir, "input.flac")
        wav_path = os.path.join(temp_dir, "output.wav")
        
        # Create FLAC file
        sample_audio.export(flac_path, format="flac")
        
        # Convert to WAV
        audio = AudioSegment.from_file(flac_path, format="flac")
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_path, format="wav")
        
        # Verify conversion
        assert os.path.exists(wav_path)
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_m4a_to_wav_conversion(self, sample_audio, temp_dir):
        """Test converting M4A to WAV format."""
        m4a_path = os.path.join(temp_dir, "input.m4a")
        wav_path = os.path.join(temp_dir, "output.wav")
        
        # Create M4A file
        sample_audio.export(m4a_path, format="ipod")
        
        # Convert to WAV
        audio = AudioSegment.from_file(m4a_path, format="m4a")
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_path, format="wav")
        
        # Verify conversion
        assert os.path.exists(wav_path)
        converted = AudioSegment.from_wav(wav_path)
        assert converted.frame_rate == 16000
        assert converted.channels == 1
    
    def test_wav_passthrough(self, sample_audio, temp_dir):
        """Test WAV files are handled correctly (no conversion needed)."""
        wav_input = os.path.join(temp_dir, "input.wav")
        wav_output = os.path.join(temp_dir, "output.wav")
        
        # Create WAV file with different sample rate
        sample_audio.set_frame_rate(44100).export(wav_input, format="wav")
        
        # Load and convert to 16kHz
        audio = AudioSegment.from_wav(wav_input)
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_output, format="wav")
        
        # Verify conversion
        assert os.path.exists(wav_output)
        converted = AudioSegment.from_wav(wav_output)
        assert converted.frame_rate == 16000
        assert converted.channels == 1


class TestFileExtensionDetection:
    """Test cases for file extension detection."""
    
    def test_wav_extension(self):
        """Test WAV file extension detection."""
        assert Path("audio.wav").suffix.lower() == ".wav"
        assert Path("audio.WAV").suffix.lower() == ".wav"
        assert Path("my.file.wav").suffix.lower() == ".wav"
    
    def test_mp3_extension(self):
        """Test MP3 file extension detection."""
        assert Path("audio.mp3").suffix.lower() == ".mp3"
        assert Path("audio.MP3").suffix.lower() == ".mp3"
    
    def test_ogg_extension(self):
        """Test OGG file extension detection."""
        assert Path("audio.ogg").suffix.lower() == ".ogg"
        assert Path("audio.OGG").suffix.lower() == ".ogg"
    
    def test_flac_extension(self):
        """Test FLAC file extension detection."""
        assert Path("audio.flac").suffix.lower() == ".flac"
        assert Path("audio.FLAC").suffix.lower() == ".flac"
    
    def test_m4a_extension(self):
        """Test M4A file extension detection."""
        assert Path("audio.m4a").suffix.lower() == ".m4a"
        assert Path("audio.M4A").suffix.lower() == ".m4a"
    
    def test_no_extension(self):
        """Test handling files with no extension."""
        assert Path("audiofile").suffix == ""


class TestAudioDuration:
    """Test cases for audio duration calculation."""
    
    def test_duration_1_second(self, temp_dir):
        """Test 1 second audio duration."""
        audio = Sine(440).to_audio_segment(duration=1000)
        assert len(audio) / 1000.0 == 1.0
    
    def test_duration_5_seconds(self, temp_dir):
        """Test 5 second audio duration."""
        audio = Sine(440).to_audio_segment(duration=5000)
        assert len(audio) / 1000.0 == 5.0
    
    def test_duration_30_seconds(self, temp_dir):
        """Test 30 second audio duration."""
        audio = Sine(440).to_audio_segment(duration=30000)
        assert len(audio) / 1000.0 == 30.0
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_duration_after_conversion(self, temp_dir):
        """Test duration is preserved after format conversion."""
        wav_path = os.path.join(temp_dir, "test.wav")
        mp3_path = os.path.join(temp_dir, "test.mp3")
        
        # Create 3 second audio
        audio = Sine(440).to_audio_segment(duration=3000)
        audio.export(wav_path, format="wav")
        
        # Convert to MP3 and back
        audio_mp3 = AudioSegment.from_wav(wav_path)
        audio_mp3.export(mp3_path, format="mp3")
        
        # Load MP3
        loaded = AudioSegment.from_mp3(mp3_path)
        duration_seconds = len(loaded) / 1000.0
        
        # Allow small variance due to encoding
        assert 2.9 <= duration_seconds <= 3.1


class TestErrorHandling:
    """Test cases for error handling."""
    
    def test_empty_file(self, temp_dir):
        """Test handling of empty file."""
        empty_path = os.path.join(temp_dir, "empty.wav")
        with open(empty_path, "wb") as f:
            pass  # Create empty file
        
        with pytest.raises(Exception):
            AudioSegment.from_wav(empty_path)
    
    def test_corrupted_file(self, temp_dir):
        """Test handling of corrupted file."""
        corrupt_path = os.path.join(temp_dir, "corrupt.wav")
        with open(corrupt_path, "wb") as f:
            f.write(b"this is not audio data")
        
        with pytest.raises(Exception):
            AudioSegment.from_wav(corrupt_path)
    
    @pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
    def test_wrong_extension(self, temp_dir):
        """Test handling file with wrong extension."""
        # Create MP3 but save as WAV extension
        audio = Sine(440).to_audio_segment(duration=1000)
        mp3_as_wav = os.path.join(temp_dir, "actually_mp3.wav")
        audio.export(mp3_as_wav, format="mp3")
        
        # Should fail when trying to load as WAV
        with pytest.raises(Exception):
            AudioSegment.from_wav(mp3_as_wav)


class TestCostCalculation:
    """Test cases for cost estimation."""
    
    def test_cost_per_minute(self):
        """Test cost calculation per minute."""
        cost_per_minute = 0.003  # $0.18/hour = $0.003/minute
        
        # 1 minute
        assert 1 * cost_per_minute == 0.003
        
        # 10 minutes
        assert 10 * cost_per_minute == 0.03
        
        # 60 minutes (1 hour)
        assert 60 * cost_per_minute == pytest.approx(0.18, rel=1e-9)
    
    def test_cost_calculation_from_seconds(self):
        """Test cost calculation from duration in seconds."""
        cost_per_minute = 0.003
        
        # 30 seconds
        duration_seconds = 30
        estimated_cost = (duration_seconds / 60) * cost_per_minute
        assert estimated_cost == pytest.approx(0.0015, rel=1e-9)
        
        # 90 seconds
        duration_seconds = 90
        estimated_cost = (duration_seconds / 60) * cost_per_minute
        assert estimated_cost == pytest.approx(0.0045, rel=1e-9)
        
        # 3600 seconds (1 hour)
        duration_seconds = 3600
        estimated_cost = (duration_seconds / 60) * cost_per_minute
        assert estimated_cost == pytest.approx(0.18, rel=1e-9)


class TestStereoToMonoConversion:
    """Test cases for stereo to mono conversion."""
    
    def test_stereo_to_mono(self, temp_dir):
        """Test converting stereo audio to mono."""
        # Create stereo audio
        left = Sine(440).to_audio_segment(duration=1000)
        right = Sine(880).to_audio_segment(duration=1000)
        stereo = AudioSegment.from_mono_audiosegments(left, right)
        
        assert stereo.channels == 2
        
        # Convert to mono
        mono = stereo.set_channels(1)
        assert mono.channels == 1
    
    def test_mono_stays_mono(self, temp_dir):
        """Test mono audio stays mono."""
        mono = Sine(440).to_audio_segment(duration=1000)
        assert mono.channels == 1
        
        mono_again = mono.set_channels(1)
        assert mono_again.channels == 1


class TestSampleRateConversion:
    """Test cases for sample rate conversion."""
    
    def test_44100_to_16000(self):
        """Test converting 44.1kHz to 16kHz."""
        audio = Sine(440).to_audio_segment(duration=1000)
        audio = audio.set_frame_rate(44100)
        assert audio.frame_rate == 44100
        
        converted = audio.set_frame_rate(16000)
        assert converted.frame_rate == 16000
    
    def test_48000_to_16000(self):
        """Test converting 48kHz to 16kHz."""
        audio = Sine(440).to_audio_segment(duration=1000)
        audio = audio.set_frame_rate(48000)
        assert audio.frame_rate == 48000
        
        converted = audio.set_frame_rate(16000)
        assert converted.frame_rate == 16000
    
    def test_16000_stays_16000(self):
        """Test 16kHz audio stays at 16kHz."""
        audio = Sine(440).to_audio_segment(duration=1000)
        audio = audio.set_frame_rate(16000)
        assert audio.frame_rate == 16000
        
        converted = audio.set_frame_rate(16000)
        assert converted.frame_rate == 16000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
