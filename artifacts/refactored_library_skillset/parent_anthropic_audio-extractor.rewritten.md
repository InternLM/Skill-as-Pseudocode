<!-- refactored skeleton for anthropic_audio-extractor (3 of 8 units replaced by child invocations (cleanup applied)) -->

# Audio Extractor
invoke(audio-extractor, {video="video files", output="WAV format"})  (parent-specific: Converts to mono 16kHz PCM format optimized for speech/energy analysis.)

## Use Cases
- Extracting audio for speech analysis
- Preparing audio for energy calculation
- Converting video audio to standard format

## Usage
invoke(audio-extractor, {video="/path/to/video.mp4", output="/path/to/audio.wav", sample-rate=16000, duration="full"})

### Parameters
- `--video`: Path to input video file
- `--output`: Path to output WAV file
- `--sample-rate`: Audio sample rate in Hz (default: 16000)
- `--duration`: Optional duration limit in seconds (default: full video)

### Output Format
- Format: WAV (PCM 16-bit signed)
- Channels: Mono
- Sample rate: 16000 Hz (default)

## Dependencies
- ffmpeg

## Example
invoke(audio-extractor, {video="lecture.mp4", output="audio.wav", duration="600"})  (parent-specific: /root/.claude/skills/audio-extractor/scripts/extract_audio.py)

## Notes
- Output is always mono for consistent analysis
- 16kHz sample rate is sufficient for speech analysis and reduces file size
- Supports any video format that ffmpeg can read