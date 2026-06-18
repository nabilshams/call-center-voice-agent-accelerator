"""
Script to generate sample audio files for testing.
Creates audio files in all supported formats: WAV, MP3, OGG, FLAC, M4A

Run this script to generate test audio files:
    python generate_test_audio.py
"""

import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from pydub import AudioSegment
    from pydub.generators import Sine, Square, WhiteNoise
except ImportError:
    print("Error: pydub is required. Install with: pip install pydub")
    sys.exit(1)

# Output directory
OUTPUT_DIR = Path(__file__).parent / "audio_samples"
OUTPUT_DIR.mkdir(exist_ok=True)


def generate_speech_like_audio(duration_ms=5000):
    """
    Generate a simple audio pattern that mimics speech characteristics.
    This creates a series of tones with pauses to simulate speech patterns.
    
    Note: This is synthetic audio for format testing, not actual speech.
    For real transcription testing, use recorded speech samples.
    """
    # Create silence
    silence = AudioSegment.silent(duration=200)
    short_silence = AudioSegment.silent(duration=100)
    
    # Create different tones to simulate speech patterns
    # Use frequencies in the speech range (85-255 Hz for fundamental, harmonics higher)
    segments = []
    
    # Simulate "Hello" - rising then falling tone
    tone1 = Sine(200).to_audio_segment(duration=150).fade_in(20).fade_out(20)
    tone2 = Sine(250).to_audio_segment(duration=100).fade_in(20).fade_out(20)
    tone3 = Sine(220).to_audio_segment(duration=200).fade_in(20).fade_out(20)
    segments.extend([tone1, tone2, tone3, silence])
    
    # Simulate "how are you" - varied tones
    tone4 = Sine(180).to_audio_segment(duration=120).fade_in(20).fade_out(20)
    tone5 = Sine(200).to_audio_segment(duration=80).fade_in(20).fade_out(20)
    tone6 = Sine(240).to_audio_segment(duration=150).fade_in(20).fade_out(20)
    segments.extend([short_silence, tone4, tone5, tone6, silence])
    
    # Simulate "today" - two syllables
    tone7 = Sine(190).to_audio_segment(duration=100).fade_in(20).fade_out(20)
    tone8 = Sine(230).to_audio_segment(duration=180).fade_in(20).fade_out(20)
    segments.extend([short_silence, tone7, short_silence, tone8, silence])
    
    # Combine all segments
    audio = segments[0]
    for seg in segments[1:]:
        audio = audio + seg
    
    # Pad to desired duration
    if len(audio) < duration_ms:
        audio = audio + AudioSegment.silent(duration=duration_ms - len(audio))
    
    # Set to speech-friendly format: 16kHz, mono, 16-bit
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    
    return audio


def generate_multi_speaker_audio(duration_ms=10000):
    """
    Generate audio that simulates multiple speakers.
    Uses different frequency ranges to represent different speakers.
    """
    # Speaker 1: Lower frequency (male-like)
    speaker1_freq = 150
    # Speaker 2: Higher frequency (female-like)
    speaker2_freq = 220
    
    silence = AudioSegment.silent(duration=300)
    short_silence = AudioSegment.silent(duration=150)
    
    segments = []
    
    # Speaker 1 segment
    s1_tone1 = Sine(speaker1_freq).to_audio_segment(duration=200).fade_in(20).fade_out(20)
    s1_tone2 = Sine(speaker1_freq + 20).to_audio_segment(duration=150).fade_in(20).fade_out(20)
    s1_tone3 = Sine(speaker1_freq + 10).to_audio_segment(duration=180).fade_in(20).fade_out(20)
    segments.extend([s1_tone1, short_silence, s1_tone2, s1_tone3, silence])
    
    # Speaker 2 segment
    s2_tone1 = Sine(speaker2_freq).to_audio_segment(duration=180).fade_in(20).fade_out(20)
    s2_tone2 = Sine(speaker2_freq + 30).to_audio_segment(duration=200).fade_in(20).fade_out(20)
    s2_tone3 = Sine(speaker2_freq + 15).to_audio_segment(duration=150).fade_in(20).fade_out(20)
    segments.extend([s2_tone1, short_silence, s2_tone2, s2_tone3, silence])
    
    # Speaker 1 again
    s1_tone4 = Sine(speaker1_freq + 5).to_audio_segment(duration=220).fade_in(20).fade_out(20)
    s1_tone5 = Sine(speaker1_freq - 10).to_audio_segment(duration=170).fade_in(20).fade_out(20)
    segments.extend([s1_tone4, short_silence, s1_tone5, silence])
    
    # Speaker 2 again
    s2_tone4 = Sine(speaker2_freq + 10).to_audio_segment(duration=200).fade_in(20).fade_out(20)
    s2_tone5 = Sine(speaker2_freq + 25).to_audio_segment(duration=180).fade_in(20).fade_out(20)
    segments.extend([s2_tone4, short_silence, s2_tone5])
    
    # Combine all segments
    audio = segments[0]
    for seg in segments[1:]:
        audio = audio + seg
    
    # Pad to desired duration
    if len(audio) < duration_ms:
        audio = audio + AudioSegment.silent(duration=duration_ms - len(audio))
    
    # Set to speech-friendly format
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    
    return audio


def export_all_formats(audio, base_name):
    """Export audio to all supported formats."""
    formats = {
        'wav': {'format': 'wav', 'parameters': ['-acodec', 'pcm_s16le']},
        'mp3': {'format': 'mp3', 'parameters': []},
        'ogg': {'format': 'ogg', 'parameters': []},
        'flac': {'format': 'flac', 'parameters': []},
        'm4a': {'format': 'ipod', 'parameters': []},  # 'ipod' is the format name for m4a in pydub
    }
    
    exported_files = []
    
    for ext, config in formats.items():
        output_path = OUTPUT_DIR / f"{base_name}.{ext}"
        try:
            audio.export(
                str(output_path),
                format=config['format'],
                parameters=config['parameters'] if config['parameters'] else None
            )
            print(f"  ✓ Created: {output_path.name}")
            exported_files.append(output_path)
        except Exception as e:
            print(f"  ✗ Failed to create {ext}: {e}")
    
    return exported_files


def create_reference_transcription(base_name, content):
    """Create a reference transcription text file."""
    txt_path = OUTPUT_DIR / f"{base_name}.txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  ✓ Created: {txt_path.name}")
    return txt_path


def main():
    print("=" * 60)
    print("Generating Test Audio Files")
    print("=" * 60)
    
    # Generate single speaker sample
    print("\n1. Creating single speaker sample...")
    single_speaker = generate_speech_like_audio(duration_ms=5000)
    export_all_formats(single_speaker, "sample_single_speaker")
    create_reference_transcription(
        "sample_single_speaker",
        "[Speaker 1]\nHello, how are you today?"
    )
    
    # Generate multi-speaker sample
    print("\n2. Creating multi-speaker sample...")
    multi_speaker = generate_multi_speaker_audio(duration_ms=8000)
    export_all_formats(multi_speaker, "sample_multi_speaker")
    create_reference_transcription(
        "sample_multi_speaker",
        "[Speaker 1]\nHello, welcome to our service.\n\n[Speaker 2]\nThank you for having me.\n\n[Speaker 1]\nHow can I help you today?\n\n[Speaker 2]\nI have a question about my account."
    )
    
    # Generate a very short sample (edge case)
    print("\n3. Creating short duration sample...")
    short_audio = generate_speech_like_audio(duration_ms=1000)
    export_all_formats(short_audio, "sample_short")
    create_reference_transcription(
        "sample_short",
        "Hello."
    )
    
    # Generate a longer sample
    print("\n4. Creating longer duration sample...")
    long_audio = generate_speech_like_audio(duration_ms=15000)
    export_all_formats(long_audio, "sample_long")
    create_reference_transcription(
        "sample_long",
        "[Speaker 1]\nThis is a longer sample audio file that is used to test the transcription service with extended duration content."
    )
    
    print("\n" + "=" * 60)
    print("Audio file generation complete!")
    print(f"Files saved to: {OUTPUT_DIR}")
    print("=" * 60)
    
    # Print summary
    print("\nGenerated files:")
    for f in sorted(OUTPUT_DIR.glob("*")):
        if f.is_file() and f.name != "README.md":
            size_kb = f.stat().st_size / 1024
            print(f"  - {f.name}: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()
