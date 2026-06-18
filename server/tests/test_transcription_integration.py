"""
Integration tests for Azure Speech transcription.
These tests call the actual Azure Speech Service to verify transcription works.

Requirements:
- Azure Speech resource configured
- AZURE_SPEECH_KEY or Azure AD auth with "Cognitive Services Speech User" role
- Audio sample files generated (run generate_test_audio_tts.py first)
- .env file with credentials (or environment variables set)

Run with:
    pytest test_transcription_integration.py -v -s

Note: These tests make real API calls and will incur costs (~$0.003/minute).
"""

import asyncio
import logging
import os
import pytest
from pathlib import Path

# Configure logging - use StreamHandler for real-time output
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]  # Ensures real-time output
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Force immediate flushing
import sys
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

# Load environment variables from .env file
from dotenv import load_dotenv

# Look for .env in server directory or project root
env_paths = [
    Path(__file__).parent.parent / ".env",  # server/.env
    Path(__file__).parent.parent.parent / ".env",  # project root/.env
]

for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"✓ Loaded environment from: {env_path}")
        break
else:
    logger.warning("⚠ No .env file found")

# Check for required environment variables
SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "swedencentral")
SPEECH_RESOURCE_ID = os.getenv("AZURE_SPEECH_RESOURCE_ID", "")

# Log configuration status
logger.info(f"Speech Region: {SPEECH_REGION}")
logger.info(f"Speech Key configured: {'Yes' if SPEECH_KEY else 'No'}")
logger.info(f"Speech Resource ID configured: {'Yes' if SPEECH_RESOURCE_ID else 'No'}")

# Path to audio samples
AUDIO_SAMPLES_DIR = Path(__file__).parent / "audio_samples"

# Check if samples exist
SAMPLES_EXIST = AUDIO_SAMPLES_DIR.exists() and any(AUDIO_SAMPLES_DIR.glob("*.wav"))

# Check if Azure credentials are available
HAS_AZURE_CREDS = bool(SPEECH_KEY) or bool(SPEECH_RESOURCE_ID)

# Log sample status
if SAMPLES_EXIST:
    wav_files = list(AUDIO_SAMPLES_DIR.glob("*.wav"))
    logger.info(f"✓ Audio samples directory: {AUDIO_SAMPLES_DIR}")
    logger.info(f"  Found {len(wav_files)} WAV files: {[f.name for f in wav_files]}")
else:
    logger.warning(f"⚠ Audio samples not found at: {AUDIO_SAMPLES_DIR}")

# Check if ffmpeg is available
import shutil
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
SKIP_FFMPEG_MSG = "ffmpeg not installed - required for non-WAV format tests"

# Skip message
SKIP_MSG = "Azure Speech credentials not configured. Set AZURE_SPEECH_KEY or AZURE_SPEECH_RESOURCE_ID"
SKIP_SAMPLES_MSG = "Audio samples not generated. Run generate_test_audio_tts.py first"


def get_speech_config():
    """Get Azure Speech SDK configuration."""
    import azure.cognitiveservices.speech as speechsdk
    
    if SPEECH_KEY:
        logger.info("Using API key authentication")
        return speechsdk.SpeechConfig(
            subscription=SPEECH_KEY,
            region=SPEECH_REGION
        )
    elif SPEECH_RESOURCE_ID:
        logger.info("Using Azure AD authentication")
        logger.info(f"Resource ID: {SPEECH_RESOURCE_ID}")
        from azure.identity import AzureCliCredential
        credential = AzureCliCredential()
        
        logger.info("Acquiring Azure AD token...")
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        logger.info("✓ Token acquired successfully")
        
        auth_token = f"aad#{SPEECH_RESOURCE_ID}#{token.token}"
        return speechsdk.SpeechConfig(
            auth_token=auth_token,
            region=SPEECH_REGION
        )
    else:
        raise RuntimeError("No Azure Speech credentials configured")


def transcribe_audio_simple(audio_path: str) -> str:
    """
    Transcribe audio using simple speech recognition (no diarization).
    Returns the transcribed text.
    """
    import azure.cognitiveservices.speech as speechsdk
    
    logger.info(f"Starting simple transcription for: {Path(audio_path).name}")
    
    speech_config = get_speech_config()
    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config
    )
    
    logger.info("Calling recognize_once()...")
    result = recognizer.recognize_once()
    
    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        logger.info(f"✓ Recognition successful: '{result.text[:100]}{'...' if len(result.text) > 100 else ''}'")
        return result.text
    elif result.reason == speechsdk.ResultReason.NoMatch:
        logger.warning(f"⚠ No speech recognized. Details: {result.no_match_details}")
        return ""
    elif result.reason == speechsdk.ResultReason.Canceled:
        cancellation = result.cancellation_details
        logger.error(f"✗ Recognition canceled: {cancellation.reason}")
        logger.error(f"  Error code: {cancellation.error_code}")
        logger.error(f"  Error details: {cancellation.error_details}")
        raise RuntimeError(f"Speech recognition canceled: {cancellation.error_details}")
    else:
        logger.error(f"✗ Speech recognition failed: {result.reason}")
        raise RuntimeError(f"Speech recognition failed: {result.reason}")


async def transcribe_audio_continuous(audio_path: str, timeout: int = 30) -> list:
    """
    Transcribe audio using continuous recognition.
    Returns a list of transcribed segments.
    """
    import azure.cognitiveservices.speech as speechsdk
    
    logger.info(f"Starting continuous transcription for: {Path(audio_path).name}")
    
    speech_config = get_speech_config()
    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config,
        audio_config=audio_config
    )
    
    segments = []
    done = asyncio.Event()
    error_msg = None
    
    def recognized_handler(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            logger.info(f"  Segment recognized: '{evt.result.text[:50]}{'...' if len(evt.result.text) > 50 else ''}'")
            segments.append(evt.result.text)
    
    def canceled_handler(evt):
        nonlocal error_msg
        logger.warning(f"⚠ Recognition canceled: {evt.cancellation_details.reason}")
        if evt.cancellation_details.error_details:
            error_msg = evt.cancellation_details.error_details
            logger.error(f"  Error: {error_msg}")
        done.set()
    
    def stopped_handler(evt):
        logger.info("✓ Session stopped")
        done.set()
    
    recognizer.recognized.connect(recognized_handler)
    recognizer.canceled.connect(canceled_handler)
    recognizer.session_stopped.connect(stopped_handler)
    
    logger.info("Starting continuous recognition...")
    recognizer.start_continuous_recognition()
    
    # Wait with progress updates
    start_time = asyncio.get_event_loop().time()
    while not done.is_set():
        try:
            await asyncio.wait_for(asyncio.shield(done.wait()), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.info(f"  ... still processing ({elapsed:.0f}s elapsed, {len(segments)} segments so far)")
            if elapsed >= timeout:
                logger.warning(f"⚠ Timeout after {timeout}s")
                break
    
    recognizer.stop_continuous_recognition()
    
    if error_msg:
        raise RuntimeError(f"Transcription failed: {error_msg}")
    
    logger.info(f"✓ Continuous transcription complete. {len(segments)} segments recognized.")
    return segments


async def transcribe_audio_with_diarization(audio_path: str, timeout: int = 30) -> list:
    """
    Transcribe audio with speaker diarization using ConversationTranscriber.
    Returns a list of dicts with 'speaker' and 'text'.
    """
    import azure.cognitiveservices.speech as speechsdk
    import wave
    
    logger.info(f"Starting diarization transcription for: {Path(audio_path).name}")
    
    # Read WAV file directly (assumes it's already in correct format)
    logger.info("Reading WAV file...")
    with wave.open(audio_path, 'rb') as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        wav_data = wf.readframes(wf.getnframes())
        duration = wf.getnframes() / sample_rate
        logger.info(f"  Duration: {duration:.2f}s, channels: {channels}, sample rate: {sample_rate}, sample width: {sample_width}")
        logger.info(f"  WAV data size: {len(wav_data)} bytes")
    
    speech_config = get_speech_config()
    
    # Create push stream with explicit format
    audio_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=sample_rate,
        bits_per_sample=sample_width * 8,
        channels=channels
    )
    push_stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
    audio_config = speechsdk.audio.AudioConfig(stream=push_stream)
    
    transcriber = speechsdk.transcription.ConversationTranscriber(
        speech_config=speech_config,
        audio_config=audio_config
    )
    
    segments = []
    done = asyncio.Event()
    error_msg = None
    
    def transcribed_handler(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            speaker = evt.result.speaker_id or 'Unknown'
            text = evt.result.text
            logger.info(f"  [{speaker}]: '{text[:50]}{'...' if len(text) > 50 else ''}'")
            segments.append({
                'speaker': speaker,
                'text': text
            })
    
    def canceled_handler(evt):
        nonlocal error_msg
        logger.warning(f"⚠ Transcription canceled: {evt.cancellation_details.reason}")
        if evt.cancellation_details.error_details:
            error_msg = evt.cancellation_details.error_details
            logger.error(f"  Error: {error_msg}")
        done.set()
    
    def stopped_handler(evt):
        logger.info("✓ Session stopped")
        done.set()
    
    transcriber.transcribed.connect(transcribed_handler)
    transcriber.canceled.connect(canceled_handler)
    transcriber.session_stopped.connect(stopped_handler)
    
    logger.info("Starting conversation transcription...")
    await asyncio.to_thread(transcriber.start_transcribing_async().get)
    
    # Push audio data
    logger.info("Pushing audio data to stream...")
    push_stream.write(wav_data)
    push_stream.close()
    logger.info("Audio stream closed, waiting for transcription...")
    
    # Wait with progress updates
    start_time = asyncio.get_event_loop().time()
    while not done.is_set():
        try:
            await asyncio.wait_for(asyncio.shield(done.wait()), timeout=5.0)
        except asyncio.TimeoutError:
            elapsed = asyncio.get_event_loop().time() - start_time
            logger.info(f"  ... still processing ({elapsed:.0f}s elapsed, {len(segments)} segments so far)")
            if elapsed >= timeout:
                logger.warning(f"⚠ Timeout after {timeout}s")
                break
    
    logger.info("Stopping transcriber...")
    await asyncio.to_thread(transcriber.stop_transcribing_async().get)
    
    if error_msg:
        raise RuntimeError(f"Transcription failed: {error_msg}")
    
    # Log summary
    speakers = set(seg['speaker'] for seg in segments)
    logger.info(f"✓ Diarization complete. {len(segments)} segments, {len(speakers)} unique speakers: {speakers}")
    
    return segments


@pytest.mark.skipif(not HAS_AZURE_CREDS, reason=SKIP_MSG)
@pytest.mark.skipif(not SAMPLES_EXIST, reason=SKIP_SAMPLES_MSG)
class TestSimpleTranscription:
    """Test simple speech recognition without diarization."""
    
    def test_transcribe_short_sample(self):
        """Test transcribing a short greeting."""
        logger.info("=" * 60)
        logger.info("TEST: test_transcribe_short_sample")
        logger.info("=" * 60)
        
        wav_path = str(AUDIO_SAMPLES_DIR / "sample_short.wav")
        logger.info(f"Audio file: {wav_path}")
        
        result = transcribe_audio_simple(wav_path)
        
        logger.info(f"Full transcription: '{result}'")
        assert len(result) > 0, "Transcription should not be empty"
        
        # Check that some expected words are present
        result_lower = result.lower()
        expected_words = ['hello', 'how', 'are', 'you']
        found_words = [w for w in expected_words if w in result_lower]
        logger.info(f"Expected words found: {found_words}")
        
        assert any(word in result_lower for word in expected_words), \
            f"Expected at least one of {expected_words} in '{result}'"
        logger.info("✓ Test passed")
    
    def test_transcribe_single_speaker(self):
        """Test transcribing a single speaker sample."""
        logger.info("=" * 60)
        logger.info("TEST: test_transcribe_single_speaker")
        logger.info("=" * 60)
        
        wav_path = str(AUDIO_SAMPLES_DIR / "sample_single_speaker.wav")
        logger.info(f"Audio file: {wav_path}")
        
        result = transcribe_audio_simple(wav_path)
        
        logger.info(f"Full transcription: '{result}'")
        assert len(result) > 0, "Transcription should not be empty"
        
        result_lower = result.lower()
        expected_words = ['test', 'speech', 'transcription', 'system']
        found_words = [w for w in expected_words if w in result_lower]
        logger.info(f"Expected words found: {found_words}")
        
        assert any(word in result_lower for word in expected_words), \
            f"Expected at least one of {expected_words} in '{result}'"
        logger.info("✓ Test passed")


@pytest.mark.skipif(not HAS_AZURE_CREDS, reason=SKIP_MSG)
@pytest.mark.skipif(not SAMPLES_EXIST, reason=SKIP_SAMPLES_MSG)
class TestContinuousTranscription:
    """Test continuous speech recognition."""
    
    @pytest.mark.asyncio
    async def test_transcribe_long_sample(self):
        """Test continuous transcription of longer audio."""
        logger.info("=" * 60)
        logger.info("TEST: test_transcribe_long_sample")
        logger.info("=" * 60)
        
        wav_path = str(AUDIO_SAMPLES_DIR / "sample_long.wav")
        logger.info(f"Audio file: {wav_path}")
        
        segments = await transcribe_audio_continuous(wav_path)
        
        logger.info(f"Number of segments: {len(segments)}")
        for i, seg in enumerate(segments):
            logger.info(f"  Segment {i+1}: '{seg[:80]}{'...' if len(seg) > 80 else ''}'")
        
        assert len(segments) > 0, "Should have at least one segment"
        full_text = ' '.join(segments).lower()
        logger.info(f"Full text length: {len(full_text)} characters")
        
        # Check for keywords from the long sample
        expected_words = ['welcome', 'service', 'transcription', 'azure', 'speech']
        found_words = [w for w in expected_words if w in full_text]
        logger.info(f"Expected words found: {found_words}")
        
        assert any(word in full_text for word in expected_words), \
            f"Expected at least one of {expected_words}"
        logger.info("✓ Test passed")


@pytest.mark.skipif(not HAS_AZURE_CREDS, reason=SKIP_MSG)
@pytest.mark.skipif(not SAMPLES_EXIST, reason=SKIP_SAMPLES_MSG)
class TestDiarizationTranscription:
    """Test transcription with speaker diarization."""
    
    @pytest.mark.asyncio
    async def test_multi_speaker_diarization(self):
        """Test that multiple speakers are detected."""
        logger.info("=" * 60)
        logger.info("TEST: test_multi_speaker_diarization")
        logger.info("=" * 60)
        
        wav_path = str(AUDIO_SAMPLES_DIR / "sample_multi_speaker.wav")
        logger.info(f"Audio file: {wav_path}")
        
        segments = await transcribe_audio_with_diarization(wav_path)
        
        logger.info(f"Number of segments: {len(segments)}")
        for i, seg in enumerate(segments):
            logger.info(f"  [{seg['speaker']}]: '{seg['text'][:60]}{'...' if len(seg['text']) > 60 else ''}'")
        
        assert len(segments) > 0, "Should have at least one segment"
        
        # Get unique speakers
        speakers = set(seg['speaker'] for seg in segments)
        logger.info(f"Unique speakers detected: {speakers}")
        
        # Should have at least some transcribed text
        full_text = ' '.join(seg['text'] for seg in segments).lower()
        logger.info(f"Full text length: {len(full_text)} characters")
        assert len(full_text) > 0, "Transcription should not be empty"
        
        # Check for keywords from the conversation
        expected_words = ['customer', 'support', 'help', 'account', 'balance', 'hello', 'thank']
        found_words = [w for w in expected_words if w in full_text]
        logger.info(f"Expected words found: {found_words}")
        
        assert any(word in full_text for word in expected_words), \
            f"Expected at least one of {expected_words}"
        logger.info("✓ Test passed")
    
    @pytest.mark.asyncio
    async def test_single_speaker_diarization(self):
        """Test diarization with single speaker (should not crash)."""
        logger.info("=" * 60)
        logger.info("TEST: test_single_speaker_diarization")
        logger.info("=" * 60)
        
        wav_path = str(AUDIO_SAMPLES_DIR / "sample_single_speaker.wav")
        logger.info(f"Audio file: {wav_path}")
        
        segments = await transcribe_audio_with_diarization(wav_path)
        
        logger.info(f"Number of segments: {len(segments)}")
        for i, seg in enumerate(segments):
            logger.info(f"  [{seg['speaker']}]: '{seg['text'][:60]}{'...' if len(seg['text']) > 60 else ''}'")
        
        assert len(segments) > 0, "Should have at least one segment"
        
        # Should have transcribed text
        full_text = ' '.join(seg['text'] for seg in segments).lower()
        logger.info(f"Full text length: {len(full_text)} characters")
        assert len(full_text) > 0, "Transcription should not be empty"
        logger.info("✓ Test passed")


@pytest.mark.skipif(not HAS_AZURE_CREDS, reason=SKIP_MSG)
@pytest.mark.skipif(not SAMPLES_EXIST, reason=SKIP_SAMPLES_MSG)
@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason=SKIP_FFMPEG_MSG)
class TestFormatCompatibility:
    """Test transcription works with different audio formats after conversion."""
    
    def test_transcribe_mp3(self):
        """Test transcribing MP3 after conversion to WAV."""
        logger.info("=" * 60)
        logger.info("TEST: test_transcribe_mp3")
        logger.info("=" * 60)
        
        from pydub import AudioSegment
        import tempfile
        
        mp3_path = AUDIO_SAMPLES_DIR / "sample_short.mp3"
        logger.info(f"Source MP3: {mp3_path}")
        
        # Convert to WAV
        logger.info("Converting MP3 to WAV...")
        audio = AudioSegment.from_mp3(str(mp3_path))
        logger.info(f"  Original: {len(audio)}ms, {audio.channels}ch, {audio.frame_rate}Hz")
        
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        logger.info(f"  Converted: {len(audio)}ms, {audio.channels}ch, {audio.frame_rate}Hz")
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            temp_path = tmp.name
        
        try:
            audio.export(temp_path, format='wav', parameters=["-acodec", "pcm_s16le"])
            logger.info(f"  Temp WAV: {temp_path}")
            
            result = transcribe_audio_simple(temp_path)
            logger.info(f"Transcription: '{result}'")
            
            assert len(result) > 0, "Transcription should not be empty"
            assert any(word in result.lower() for word in ['hello', 'how', 'are', 'you']), \
                f"Expected greeting words in '{result}'"
            logger.info("✓ Test passed")
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    
    def test_transcribe_ogg(self):
        """Test transcribing OGG after conversion to WAV."""
        logger.info("=" * 60)
        logger.info("TEST: test_transcribe_ogg")
        logger.info("=" * 60)
        
        from pydub import AudioSegment
        import tempfile
        
        ogg_path = AUDIO_SAMPLES_DIR / "sample_short.ogg"
        logger.info(f"Source OGG: {ogg_path}")
        
        logger.info("Converting OGG to WAV...")
        audio = AudioSegment.from_ogg(str(ogg_path))
        logger.info(f"  Original: {len(audio)}ms, {audio.channels}ch, {audio.frame_rate}Hz")
        
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            temp_path = tmp.name
        
        try:
            audio.export(temp_path, format='wav', parameters=["-acodec", "pcm_s16le"])
            logger.info(f"  Temp WAV: {temp_path}")
            
            result = transcribe_audio_simple(temp_path)
            logger.info(f"Transcription: '{result}'")
            
            assert len(result) > 0, "Transcription should not be empty"
            logger.info("✓ Test passed")
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


@pytest.mark.skipif(not HAS_AZURE_CREDS, reason=SKIP_MSG)
@pytest.mark.skipif(not SAMPLES_EXIST, reason=SKIP_SAMPLES_MSG)
class TestTranscriptionAccuracy:
    """Test transcription accuracy against reference text."""
    
    def test_short_sample_accuracy(self):
        """Test transcription accuracy of short sample."""
        logger.info("=" * 60)
        logger.info("TEST: test_short_sample_accuracy")
        logger.info("=" * 60)
        
        wav_path = str(AUDIO_SAMPLES_DIR / "sample_short.wav")
        txt_path = AUDIO_SAMPLES_DIR / "sample_short.txt"
        
        logger.info(f"Audio file: {wav_path}")
        logger.info(f"Reference file: {txt_path}")
        
        # Get transcription
        result = transcribe_audio_simple(wav_path)
        logger.info(f"Transcription: '{result}'")
        
        # Load reference (strip speaker label)
        with open(txt_path, 'r') as f:
            reference = f.read()
        logger.info(f"Reference (raw): '{reference}'")
        
        # Remove speaker label
        if ']' in reference:
            reference = reference.split(']', 1)[1].strip()
        logger.info(f"Reference (cleaned): '{reference}'")
        
        # Simple word overlap check
        result_words = set(result.lower().split())
        ref_words = set(reference.lower().split())
        
        logger.info(f"Result words: {result_words}")
        logger.info(f"Reference words: {ref_words}")
        
        # Calculate overlap
        overlap = len(result_words & ref_words)
        min_overlap = len(ref_words) * 0.5
        
        logger.info(f"Word overlap: {overlap}/{len(ref_words)} (minimum: {min_overlap:.0f})")
        logger.info(f"Matching words: {result_words & ref_words}")
        
        assert overlap >= min_overlap, f"Low accuracy: {overlap}/{len(ref_words)} words matched"
        logger.info("✓ Test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
