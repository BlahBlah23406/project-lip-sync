# Project Lipsync: Audio Pipeline Optimization Research

## Executive Summary

After analyzing the current pipeline and researching state-of-the-art techniques, I've identified several key areas for improvement that could significantly enhance the quality of automated Islamic content dubbing.

---

## Current Pipeline Analysis

### Architecture Overview
1. **Caption Extraction**: YouTube transcript API → clustering (0.4s gap, 8s max)
2. **Translation**: Anthropic Claude/Ollama batch with speaker diarization
3. **TTS**: Microsoft Edge TTS (4 Bangla voices)
4. **Rate Control**: Reprompt at >1.3x, atempo for 1.02-2.0x
5. **Mixing**: FFmpeg amix with dynamic volume ducking (12% during speech)

### Current Strengths
- Smart clustering for natural speech flow
- Speaker diarization with multiple voices
- Arabic quote preservation (original audio slicing)
- Dynamic background ducking with crossfade
- Reprompting when translations exceed time windows

### Current Weaknesses
1. **Edge TTS is basic**: No emotional/prosodic control, limited voice variety
2. **Simple atempo for speedup**: Creates artifacts at >1.2x, chipmunk effect at high rates
3. **Fixed chars/sec ratio**: 10-12 chars/sec doesn't account for phonetic density
4. **No audio codec optimization**: Using MP3/AAC instead of neural codecs
5. **No loudness normalization**: Inconsistent volume across segments
6. **No voice consistency**: Each speaker uses fixed voice, no cloning

---

## Research Findings: State of the Art

### 1. Neural Audio Codecs (Compression)

**Best Options:**
- **Descript Audio Codec (DAC)**: 90x compression at 8kbps, MIT licensed
  - 44.1kHz native support with minimal artifacts
  - Drop-in replacement for EnCodec
  - `pip install descript-audio-codec`
  
- **Meta's EnCodec**: Open source, various bitrates (1.5-24 kbps)
  - Good for streaming applications
  - Real-time capable

**Benefits for Pipeline:**
- Smaller file sizes for storage/distribution
- Better quality at lower bitrates
- Can be used as intermediate format for processing

### 2. High-Quality Time Stretching

**Current vs. SOTA:**
- `atempo`: Simple, fast, poor quality at high rates
- `rubberband`: Professional-grade, preserves transients
  - Library from Breakfast Quay (used by professional DAWs)
  - Better phase coherence for speech
  - Multiple modes: "MusiCally" vs "Speech" optimized

**FFmpeg Integration:**
```ffmpeg
# Current (poor quality at high rates)
atempo=1.5

# Better (requires rubberband compiled in)
rubberband=tempo=1.5:transients=smooth
```

### 3. Advanced Audio Processing

**Loudness Normalization (EBU R128):**
- Standard for broadcast audio
- Consistent perceived loudness across segments
- FFmpeg filter: `loudnorm`

**Dynamic Range Compression:**
- Controls peaks while maintaining body
- Prevents clipping during mixing
- FFmpeg filter: `acompressor`

### 4. TTS Improvements

**Current Edge TTS Limitations:**
- No fine-grained prosodic control
- Limited emotional range
- Fixed voices (no cloning)

**Research Findings:**
- **Voice Cloning**: Requires training data, but could create consistent voices per speaker
- **Pitch/Rate Variation**: Better TTS APIs (OpenAI, ElevenLabs) offer prosodic control
- **Speaking Rate in TTS**: Some TTS engines allow rate control natively, avoiding post-processing

### 5. Intelligent Rate Control

**Current**: Fixed 10-12 chars/sec

**Better Approach:**
- Syllable-based timing (Bangla: ~5-6 syllables/sec natural)
- Phonetic density analysis: "দুঃখ" (duhkha) takes longer than "ক" (ka)
- Machine learning model to predict audio duration from text

---

## Proposed Experiment Design

### Test Audio Sample
3-minute English Islamic lecture segment with:
- Multiple speakers (host + guest)
- Arabic quote recitation
- Variable speech rates
- Background audio/music

### Experiments to Run

#### Experiment 1: Baseline (Current Pipeline)
- Edge TTS with atempo speedup
- Current clustering (0.4s, 8s max)
- Current mixing (12% ducking)

#### Experiment 2: Rubberband Time Stretching
- Same as baseline but replace atempo with rubberband
- Compare quality at 1.1x, 1.3x, 1.5x speedup

#### Experiment 3: Loudness Normalization
- Add EBU R128 loudnorm filter before mixing
- Apply to all TTS segments

#### Experiment 4: Dynamic Range Compression
- Add acompressor to chain
- Test different threshold/ratio settings

#### Experiment 5: Combined Optimizations
- Rubberband + loudnorm + acompressor
- Best settings from individual experiments

#### Experiment 6: Alternative Clustering
- Test 0.3s gap, 0.5s gap
- Test 6s, 10s max duration
- Measure naturalness vs timing accuracy

#### Experiment 7: Adaptive Rate Control
- Syllable-based timing prediction
- Compare against fixed 10-12 chars/sec

### Metrics to Collect

**Objective Metrics:**
1. Processing time per experiment
2. Output file size
3. Speedup factor distribution across segments
4. Segment overlap instances
5. Audio duration prediction accuracy

**Subjective Metrics (Listening Tests):**
1. Naturalness of speech (1-5 scale)
2. Intelligibility at speed (1-5 scale)
3. Background audio blend quality (1-5 scale)
4. Speaker distinction clarity (1-5 scale)
5. Overall listening experience (1-5 scale)

### Tools Required

```bash
# Already available
ffmpeg with standard filters

# To install
pip install descript-audio-codec  # Neural audio codec
# Note: rubberband requires custom ffmpeg build or standalone binary
```

---

## Implementation Plan

1. **Create test harness**: Extract 3-minute sample from YouTube
2. **Build experiment runner**: Automated testing framework
3. **Run baseline**: Document current performance
4. **Run experiments 2-7**: Sequential, with metrics collection
5. **Analyze results**: Identify best configuration
6. **Document recommendations**: Final report

---

## Expected Outcomes

**Conservative Improvements (90% confidence):**
- 20-30% better quality at speedup rates >1.3x (rubberband)
- More consistent volume across output (loudnorm)
- Reduced clipping/peaks (acompressor)

**Optimistic Improvements (50% confidence):**
- 40-50% quality improvement with combined optimizations
- Better naturalness through adaptive rate control
- Smaller file sizes with neural codecs

**Risk Areas:**
- Rubberband may require recompilation of FFmpeg
- Neural codecs add processing overhead
- Syllable-based timing needs Bangla language analysis

---

## Next Steps

1. Set up test environment with 3-minute sample
2. Implement experiment runner
3. Execute baseline run
4. Proceed with optimization experiments

*Research completed: [DATE]*
