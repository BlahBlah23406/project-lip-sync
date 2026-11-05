#!/usr/bin/env python
"""
Pitch Extraction Benchmark
===========================
Implements F0 pitch contour extraction using torchcrepe (tiny model) and
PyWorld as a reference, on Bengali audio clips from the LipSync project.

Output: project-lip-sync/pitch_extraction/
"""

import os
import sys
import json
import glob
import pathlib
import numpy as np
from datetime import datetime

os.environ.setdefault("PYTHONUTF8", "1")

OUTPUT_DIR = pathlib.Path(__file__).parent / "pitch_extraction"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Audio sample sources
DUBBED_OUTPUT = pathlib.Path(__file__).parent / "output"
TEST_WAVS = [
    pathlib.Path(__file__).parent / "test_dubbed_3m.wav",
    pathlib.Path(__file__).parent / "test_dubbed_3m_v2.wav",
    pathlib.Path(__file__).parent / "test_original_3m.wav",
    pathlib.Path(__file__).parent / "test_original_3m_v2.wav",
]

# Sample rate and hop size
SAMPLE_RATE = 16000
HOP_MS = 10  # 10ms hop
HOP_SAMPLES = int(SAMPLE_RATE * HOP_MS / 1000)  # 160 samples
MAX_CLIPS = 5
CLIP_DURATION_SEC = 10  # Analyze first 10 seconds of each clip


def find_audio_clips():
    """Find Bengali audio clips for testing."""
    clips = []
    
    # Check test WAVs
    for w in TEST_WAVS:
        if w.exists():
            clips.append(str(w))
    
    # Look for dubbed MP3s in output directories
    if DUBBED_OUTPUT.exists():
        for mp3 in sorted(DUBBED_OUTPUT.glob("*_dubbed.mp3")):
            clips.append(str(mp3))
            if len(clips) >= MAX_CLIPS * 2:  # Collect extra in case some fail
                break
    
    # Also look for individual TTS segments
    tts_dir = pathlib.Path(__file__).parent / "output"
    if tts_dir.exists():
        for d in sorted(tts_dir.iterdir()):
            if d.is_dir():
                for seg in d.glob("tts_*.mp3"):
                    clips.append(str(seg))
                    if len(clips) >= MAX_CLIPS * 3:
                        break
                if len(clips) >= MAX_CLIPS * 3:
                    break
    
    return clips[:MAX_CLIPS * 2]


def load_audio(filepath, sr=SAMPLE_RATE, duration_sec=CLIP_DURATION_SEC):
    """Load audio file and return samples at target sample rate."""
    try:
        import librosa
        y, orig_sr = librosa.load(filepath, sr=sr, duration=duration_sec, mono=True)
        return y, sr
    except ImportError:
        # Fallback: use soundfile + resampling
        try:
            import soundfile as sf
            y, orig_sr = sf.read(filepath, dtype='float32')
            if y.ndim > 1:
                y = y.mean(axis=1)
            if orig_sr != sr:
                # Simple resampling
                ratio = sr / orig_sr
                n_samples = int(len(y) * ratio)
                indices = np.linspace(0, len(y) - 1, n_samples)
                y = np.interp(indices, np.arange(len(y)), y)
            return y[:sr * duration_sec], sr
        except ImportError:
            print(f"  ERROR: Need librosa or soundfile to load audio")
            return None, sr


def extract_pitch_torchcrepe(y, sr, model='tiny'):
    """Extract F0 using torchcrepe."""
    try:
        import torch
        import torchcrepe
    except ImportError:
        print("  torchcrepe not installed, installing...")
        os.system(f"{sys.executable} -m pip install torchcrepe --quiet")
        import torch
        import torchcrepe
    
    # torchcrepe expects (batch, samples) tensor
    audio = torch.tensor(y, dtype=torch.float32).unsqueeze(0)
    
    hop_length = HOP_SAMPLES
    
    # Get pitch predictions
    pitch, periodicity = torchcrepe.predict(
        audio,
        sample_rate=sr,
        hop_length=hop_length,
        fmin=50,
        fmax=400,
        model=model,
        batch_size=1024,
        device='cpu',
        return_periodicity=True,
    )
    
    # Convert to numpy
    pitch = pitch.squeeze(0).numpy()
    periodicity = periodicity.squeeze(0).numpy()
    
    # Filter unvoiced frames (periodicity threshold)
    voiced = periodicity > 0.5
    pitch_filtered = np.where(voiced, pitch, 0.0)
    
    # Build output array
    times_ms = np.arange(len(pitch)) * HOP_MS
    contour = []
    for i in range(len(pitch)):
        contour.append({
            'time_ms': int(times_ms[i]),
            'f0_hz': round(float(pitch_filtered[i]), 2),
            'voiced': bool(voiced[i]),
            'periodicity': round(float(periodicity[i]), 4),
        })
    
    return contour


def extract_pitch_pyworld(y, sr):
    """Extract F0 using PyWorld (Harvest + StoneMask)."""
    try:
        import pyworld as pw
    except ImportError:
        print("  pyworld not installed, installing...")
        os.system(f"{sys.executable} -m pip install pyworld --quiet")
        import pyworld as pw
    
    # PyWorld expects float64
    y_f64 = y.astype(np.float64)
    
    # Extract F0 using Harvest (more accurate than Dio)
    f0, time_axis = pw.harvest(
        y_f64, sr,
        frame_period=HOP_MS,
        f0_floor=50.0,
        f0_ceil=400.0,
    )
    
    # Refine F0 using StoneMask
    f0_refined = pw.stonemask(y_f64, f0, time_axis, sr)
    
    # Voiced/unvoiced
    voiced = f0_refined > 0
    
    contour = []
    for i in range(len(f0_refined)):
        contour.append({
            'time_ms': int(time_axis[i] * 1000),
            'f0_hz': round(float(f0_refined[i]), 2),
            'voiced': bool(voiced[i]),
        })
    
    return contour


def compare_contours(crepe_contour, world_contour):
    """Compare two F0 contours and return correlation metrics."""
    # Align by time_ms
    crepe_by_time = {c['time_ms']: c for c in crepe_contour}
    world_by_time = {c['time_ms']: c for c in world_contour}
    
    common_times = sorted(set(crepe_by_time.keys()) & set(world_by_time.keys()))
    
    if len(common_times) < 2:
        return {'error': 'Not enough overlapping frames'}
    
    crepe_f0 = []
    world_f0 = []
    for t in common_times:
        c = crepe_by_time[t]
        w = world_by_time[t]
        if c['voiced'] and w['voiced']:
            crepe_f0.append(c['f0_hz'])
            world_f0.append(w['f0_hz'])
    
    if len(crepe_f0) < 2:
        return {
            'voiced_frames_in_common': len(crepe_f0),
            'correlation': None,
            'mean_abs_error_hz': None,
            'note': 'Too few voiced frames in common'
        }
    
    crepe_arr = np.array(crepe_f0)
    world_arr = np.array(world_f0)
    
    correlation = float(np.corrcoef(crepe_arr, world_arr)[0, 1])
    mean_abs_error = float(np.mean(np.abs(crepe_arr - world_arr)))
    mean_error = float(np.mean(crepe_arr - world_arr))
    rms_error = float(np.sqrt(np.mean((crepe_arr - world_arr) ** 2)))
    
    return {
        'voiced_frames_in_common': len(crepe_f0),
        'correlation': round(correlation, 4),
        'mean_abs_error_hz': round(mean_abs_error, 2),
        'mean_error_hz': round(mean_error, 2),
        'rms_error_hz': round(rms_error, 2),
    }


def run_benchmark():
    """Main pitch extraction benchmark."""
    print(f"Pitch Extraction Benchmark")
    print(f"=" * 60)
    print(f"Output: {OUTPUT_DIR}")
    print()
    
    clips = find_audio_clips()
    print(f"Found {len(clips)} audio clips (using up to {MAX_CLIPS})")
    
    if not clips:
        print("ERROR: No audio clips found!")
        return
    
    results = []
    
    for i, clip_path in enumerate(clips[:MAX_CLIPS]):
        clip_name = pathlib.Path(clip_path).stem
        print(f"\n--- Clip {i+1}/{min(len(clips), MAX_CLIPS)}: {clip_name} ---")
        
        # Load audio
        y, sr = load_audio(clip_path)
        if y is None:
            print(f"  SKIP: Could not load audio")
            continue
        
        print(f"  Loaded {len(y)/sr:.1f}s at {sr}Hz")
        
        # Extract with torchcrepe
        print(f"  Extracting with torchcrepe (tiny)...", end=" ", flush=True)
        try:
            crepe_contour = extract_pitch_torchcrepe(y, sr, model='tiny')
            print(f"{len(crepe_contour)} frames")
        except Exception as e:
            print(f"ERROR: {e}")
            crepe_contour = None
        
        # Extract with PyWorld
        print(f"  Extracting with PyWorld...", end=" ", flush=True)
        try:
            world_contour = extract_pitch_pyworld(y, sr)
            print(f"{len(world_contour)} frames")
        except Exception as e:
            print(f"ERROR: {e}")
            world_contour = None
        
        # Save contours
        clip_result = {
            'clip': clip_name,
            'path': clip_path,
            'duration_sec': len(y) / sr,
            'sample_rate': sr,
            'crepe_frames': len(crepe_contour) if crepe_contour else 0,
            'world_frames': len(world_contour) if world_contour else 0,
        }
        
        if crepe_contour:
            with open(OUTPUT_DIR / f"{clip_name}_crepe_f0.json", 'w', encoding='utf-8') as f:
                json.dump(crepe_contour, f, indent=2)
        
        if world_contour:
            with open(OUTPUT_DIR / f"{clip_name}_world_f0.json", 'w', encoding='utf-8') as f:
                json.dump(world_contour, f, indent=2)
        
        # Compare
        if crepe_contour and world_contour:
            comparison = compare_contours(crepe_contour, world_contour)
            clip_result['comparison'] = comparison
            print(f"  Comparison: corr={comparison.get('correlation', 'N/A')} MAE={comparison.get('mean_abs_error_hz', 'N/A')}Hz")
        
        # Basic F0 statistics
        if crepe_contour:
            voiced_f0 = [c['f0_hz'] for c in crepe_contour if c['voiced'] and c['f0_hz'] > 0]
            if voiced_f0:
                clip_result['crepe_stats'] = {
                    'mean_f0': round(float(np.mean(voiced_f0)), 2),
                    'median_f0': round(float(np.median(voiced_f0)), 2),
                    'std_f0': round(float(np.std(voiced_f0)), 2),
                    'min_f0': round(float(np.min(voiced_f0)), 2),
                    'max_f0': round(float(np.max(voiced_f0)), 2),
                    'voiced_ratio': round(len(voiced_f0) / len(crepe_contour), 3),
                }
        
        if world_contour:
            voiced_f0 = [c['f0_hz'] for c in world_contour if c['voiced'] and c['f0_hz'] > 0]
            if voiced_f0:
                clip_result['world_stats'] = {
                    'mean_f0': round(float(np.mean(voiced_f0)), 2),
                    'median_f0': round(float(np.median(voiced_f0)), 2),
                    'std_f0': round(float(np.std(voiced_f0)), 2),
                    'min_f0': round(float(np.min(voiced_f0)), 2),
                    'max_f0': round(float(np.max(voiced_f0)), 2),
                    'voiced_ratio': round(len(voiced_f0) / len(world_contour), 3),
                }
        
        results.append(clip_result)
    
    # Summary
    summary = {
        'benchmark_date': datetime.now().isoformat(),
        'sample_rate': SAMPLE_RATE,
        'hop_ms': HOP_MS,
        'clip_duration_sec': CLIP_DURATION_SEC,
        'total_clips': len(results),
        'clips': results,
        'models': {
            'primary': 'torchcrepe tiny (onnxcrepe recommended for on-device)',
            'reference': 'PyWorld Harvest + StoneMask',
        },
        'note': 'F0 contours extracted at 10ms hop, 50-400 Hz range. Comparison uses only frames where both models report voicing. On-device deployment should use onnxcrepe tiny via ONNX Runtime (already a dependency via sherpa_onnx).',
    }
    
    with open(OUTPUT_DIR / "summary.json", 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)
    
    print(f"\n{'=' * 60}")
    print(f"PITCH EXTRACTION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Clips processed: {len(results)}")
    for r in results:
        comp = r.get('comparison', {})
        corr = comp.get('correlation', 'N/A')
        mae = comp.get('mean_abs_error_hz', 'N/A')
        print(f"  {r['clip']}: {r['duration_sec']:.1f}s | crepe={r['crepe_frames']} frames | corr={corr} MAE={mae}Hz")
    print(f"\nResults saved to: {OUTPUT_DIR}")
    
    return summary


if __name__ == "__main__":
    run_benchmark()
