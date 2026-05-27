<!-- refactored skeleton for anthropic_audiobook (5 of 19 units replaced by child invocations (cleanup applied)) -->

# Audiobook Creation Guide
Create audiobooks from web articles, essays, or text files. This skill covers the full pipeline: content fetching, text processing, and audio generation.

## Quick Start
```python
import os

# 1. Check which TTS API is available
def get_tts_provider():
    if os.environ.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    elif os.environ.get("OPENAI_API_KEY"):
        return "openai"
    else:
        return "gtts"  # Free, no API key needed

provider = get_tts_provider()
print(f"Using TTS provider: {provider}")
```

## Step 1: Fetching Web Content

### IMPORTANT: Verify fetched content is complete
WebFetch and similar tools may return summaries instead of full text. Always verify:

```python
import subprocess

def fetch_article_content(url):
    """Fetch article content using curl for reliability."""
    # Use curl to get raw HTML - more reliable than web fetch tools
    result = subprocess.run(
        ["curl", "-s", url],
        capture_output=True,
        text=True
    )
    html = result.stdout

    # Strip HTML tags (basic approach)
    import re
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    return text
```

### Content verification checklist
Before converting to audio, verify:
- [ ] Text length is reasonable for the source (articles typically 1,000-10,000+ words)
- [ ] Content includes actual article text, not just navigation/headers
- [ ] No "summary" or "key points" headers that indicate truncation

```python
def verify_content(text, expected_min_chars=1000):
    """Basic verification that content is complete."""
    if len(text) < expected_min_chars:
        print(f"WARNING: Content may be truncated ({len(text)} chars)")
        return False
    if "summary" in text.lower()[:500] or "key points" in text.lower()[:500]:
        print("WARNING: Content appears to be a summary, not full text")
        return False
    return True
```

## Step 2: Text Processing

### Clean and prepare text for TTS
```python
import re

def clean_text_for_tts(text):
    """Clean text for better TTS output."""
    # Remove URLs
    text = re.sub(r'http[s]?://\S+', '', text)

    # Remove footnote markers like [1], [2]
    text = re.sub(r'\[\d+\]', '', text)

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)

    # Remove special characters that confuse TTS
    text = re.sub(r'[^\w\s.,!?;:\'"()-]', '', text)

    return text.strip()

def chunk_text(text, max_chars=4000):
    """Split text into chunks at sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""

    for sentence in sentences:
        if len(current_chunk) + len(sentence) < max_chars:
            current_chunk += sentence + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence + " "

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks
```

## Step 3: TTS Conversion with Fallback

### Automatic provider selection
invoke(tts-provider-selection, {text="text", output_path="output_path"})

### ElevenLabs implementation
invoke(tts-provider-selection, {text="text", output_path="output_path"})  (parent-specific: api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel - calm female voice…)

### OpenAI TTS implementation
invoke(tts-provider-selection, {text="text", output_path="output_path"})

### gTTS implementation (free fallback)
invoke(tts-provider-selection, {text="text", output_path="output_path"})

### Audio concatenation
invoke(audio-concatenation, {audio_files="audio_files", output_path="output_path"})

## Complete Example
```python
#!/usr/bin/env python3
"""Create audiobook from web articles."""

import os
import re
import subprocess
import requests

# ... include all helper functions above ...

def main():
    # Fetch articles
    urls = [
        "https://example.com/article1",
        "https://example.com/article2"
    ]

    all_text = ""
    for url in urls:
        print(f"Fetching: {url}")
        text = fetch_article_content(url)

        if not verify_content(text):
            print(f"WARNING: Content from {url} may be incomplete")

        all_text += f"\n\n{text}"

    # Clean and convert
    clean_text = clean_text_for_tts(all_text)
    print(f"Total text: {len(clean_text)} characters")

    # Create audiobook
    success = invoke(audio-concatenation, {audio_files="audio_files", output_path="/root/audiobook.mp3"})

    if success:
        print("Audiobook created successfully!")
    else:
        print("Failed to create audiobook")

if __name__ == "__main__":
    main()
```

## TTS Provider Comparison
| Provider | Quality | Cost | API Key Required | Best For |
|----------|---------|------|------------------|----------|
| ElevenLabs | Excellent | Paid | Yes | Professional audiobooks |
| OpenAI TTS | Very Good | Paid | Yes | General purpose |
| gTTS | Good | Free | No | Testing, budget projects |

## Troubleshooting

### "Content appears to be a summary"
- Use `curl` directly instead of web fetch tools
- Verify the URL is correct and accessible
- Check if the site requires JavaScript rendering

### "API key not found"
- Check environment variables: `echo $OPENAI_API_KEY`
- Ensure keys are exported in the shell
- Fall back to gTTS if no paid API keys available

### "Audio chunks don't sound continuous"
- Ensure chunking happens at sentence boundaries
- Consider adding small pauses between sections
- Use consistent voice settings across all chunks