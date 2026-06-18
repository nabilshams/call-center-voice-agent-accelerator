"""
Generate test audio samples with actual speech for transcription testing.
Uses gTTS (Google Text-to-Speech) to create audio with spoken words.

Run this script to generate audio samples:
    python generate_test_audio_tts.py
"""

import os
import tempfile
from pathlib import Path
from pydub import AudioSegment

# Check if gTTS is available
try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False
    print("Warning: gTTS not installed. Run: pip install gTTS")


def text_to_audio(text: str, lang: str = 'en') -> AudioSegment:
    """Convert text to audio using Google TTS."""
    if not GTTS_AVAILABLE:
        raise RuntimeError("gTTS is not installed. Run: pip install gTTS")
    
    # Create TTS audio
    tts = gTTS(text=text, lang=lang, slow=False)
    
    # Save to temp file and load with pydub
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
        tmp_path = tmp.name
    
    try:
        tts.save(tmp_path)
        audio = AudioSegment.from_mp3(tmp_path)
        
        # Convert to speech SDK compatible format: 16kHz, mono, 16-bit
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        
        return audio
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def create_multi_speaker_audio(speaker1_texts: list, speaker2_texts: list) -> AudioSegment:
    """Create audio simulating a conversation between two speakers."""
    silence = AudioSegment.silent(duration=500)  # 500ms pause between speakers
    
    combined = AudioSegment.empty()
    
    # Interleave speakers
    max_turns = max(len(speaker1_texts), len(speaker2_texts))
    
    for i in range(max_turns):
        if i < len(speaker1_texts):
            audio1 = text_to_audio(speaker1_texts[i])
            combined += audio1 + silence
        
        if i < len(speaker2_texts):
            audio2 = text_to_audio(speaker2_texts[i])
            combined += audio2 + silence
    
    return combined


def export_all_formats(audio: AudioSegment, base_path: Path, name: str):
    """Export audio to all supported formats."""
    formats = {
        'wav': {'format': 'wav', 'parameters': ["-acodec", "pcm_s16le"]},
        'mp3': {'format': 'mp3', 'parameters': ["-q:a", "2"]},
        'ogg': {'format': 'ogg', 'parameters': []},
        'flac': {'format': 'flac', 'parameters': []},
        'm4a': {'format': 'ipod', 'parameters': ["-acodec", "aac"]},
    }
    
    for ext, config in formats.items():
        output_path = base_path / f"{name}.{ext}"
        audio.export(
            str(output_path),
            format=config['format'],
            parameters=config['parameters']
        )
        print(f"  Created: {output_path.name} ({output_path.stat().st_size / 1024:.1f} KB)")


def create_reference_transcription(base_path: Path, name: str, text: str):
    """Create a reference transcription file."""
    txt_path = base_path / f"{name}.txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f"  Created: {txt_path.name}")


def main():
    """Generate all test audio samples with actual speech."""
    if not GTTS_AVAILABLE:
        print("ERROR: gTTS is required. Install with: pip install gTTS")
        return
    
    # Get the audio_samples directory
    samples_dir = Path(__file__).parent / "audio_samples"
    samples_dir.mkdir(exist_ok=True)
    
    print("Generating test audio samples with speech...\n")
    
    # 1. Single speaker sample - a simple statement
    print("1. Single speaker sample:")
    single_text = "Hello, this is a test of the speech transcription system. I am speaking clearly so the system can recognize my words accurately."
    single_speaker = text_to_audio(single_text)
    export_all_formats(single_speaker, samples_dir, "sample_single_speaker")
    create_reference_transcription(
        samples_dir, 
        "sample_single_speaker",
        f"[Speaker 1] {single_text}"
    )
    
    # 2. Multi-speaker sample - a customer service conversation
    print("\n2. Multi-speaker sample:")
    speaker1_texts = [
        "Hello, thank you for calling customer support. How can I help you today?",
        "I understand. Let me look into that for you.",
        "I found your account. Your balance is one hundred fifty dollars."
    ]
    speaker2_texts = [
        "Hi, I need to check my account balance please.",
        "Thank you so much for your help."
    ]
    
    multi_speaker = create_multi_speaker_audio(speaker1_texts, speaker2_texts)
    export_all_formats(multi_speaker, samples_dir, "sample_multi_speaker")
    
    # Build reference transcription
    ref_lines = []
    max_turns = max(len(speaker1_texts), len(speaker2_texts))
    for i in range(max_turns):
        if i < len(speaker1_texts):
            ref_lines.append(f"[Speaker 1] {speaker1_texts[i]}")
        if i < len(speaker2_texts):
            ref_lines.append(f"[Speaker 2] {speaker2_texts[i]}")
    
    create_reference_transcription(
        samples_dir,
        "sample_multi_speaker", 
        "\n".join(ref_lines)
    )
    
    # 3. Short sample - just a greeting
    print("\n3. Short sample:")
    short_text = "Hello, how are you today?"
    short_audio = text_to_audio(short_text)
    export_all_formats(short_audio, samples_dir, "sample_short")
    create_reference_transcription(
        samples_dir,
        "sample_short",
        f"[Speaker 1] {short_text}"
    )
    
    # 4. Long sample - a detailed explanation
    print("\n4. Long sample:")
    long_text = "Welcome to our service. Today I will explain how our transcription system works. First, you upload an audio file in any supported format such as WAV, MP3, OGG, FLAC, or M4A. The system then converts it to the correct format for processing. Next, the Azure Speech Service analyzes the audio and identifies different speakers. Finally, you receive a complete transcription with speaker labels and timestamps. You can also compare the results against a reference text to measure accuracy."
    
    long_audio = text_to_audio(long_text)
    export_all_formats(long_audio, samples_dir, "sample_long")
    create_reference_transcription(
        samples_dir,
        "sample_long",
        f"[Speaker 1] {long_text}"
    )
    
    print("\n✅ All audio samples with speech generated successfully!")
    print(f"   Location: {samples_dir}")
    print("\n   These files contain actual spoken words for transcription testing.")


if __name__ == "__main__":
    main()
