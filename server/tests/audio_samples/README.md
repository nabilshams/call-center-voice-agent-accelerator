# Test Audio Samples

This folder contains sample audio files for testing the transcription functionality.

## Supported Formats

- **WAV** - Uncompressed PCM audio (best quality, largest file size)
- **MP3** - Compressed audio (good quality, smaller file size)
- **OGG** - Open source compressed format (Vorbis codec)
- **FLAC** - Lossless compression (high quality, medium file size)
- **M4A** - AAC compressed audio (good quality, small file size)

## Reference Transcriptions

Each audio file has a corresponding `.txt` file with the expected transcription.

## Generating Test Files

Run the `generate_test_audio.py` script to create sample audio files:

```bash
cd server/tests
python generate_test_audio.py
```

This will create synthetic audio files for format testing. For actual speech recognition testing, replace these with real recorded audio samples.

## File Naming Convention

- `sample_speech.{format}` - Audio file in various formats
- `sample_speech.txt` - Reference transcription text
