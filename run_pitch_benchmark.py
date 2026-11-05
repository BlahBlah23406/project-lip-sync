"""Tiny CREPE versus WORLD on five reproducible ten-second audio samples."""
import json
import shutil
from pathlib import Path
import numpy as np
import librosa
import torch
import torchcrepe
import pyworld as pw

ROOT = Path(__file__).resolve().parent
if ROOT.name == 'pitch_extraction':
    ROOT = ROOT.parent
OUT = ROOT / 'pitch_extraction'

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def candidates():
    output = ROOT / 'output'
    ordered = sorted(output.glob('*/*dubbed*.mp3'))
    test = ROOT / 'test_dubbed_3m.wav'
    if test.exists():
        ordered.append(test)
    # Root-level dubbed exports precede generic intermediate audio within tier 3.
    ordered += sorted(output.glob('*dubbed*.mp3'))
    ordered += sorted(p for p in output.rglob('*') if p.suffix.lower() in ('.wav', '.mp3'))
    return list(dict.fromkeys(ordered))

def contour(f0, voiced):
    return [{'time_ms': i * 10, 'f0_hz': float(value) if voiced[i] else 0.0,
             'voiced': bool(voiced[i])} for i, value in enumerate(f0)]

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if Path(__file__).resolve() != OUT / 'run_pitch_benchmark.py':
        shutil.copy2(__file__, OUT / 'run_pitch_benchmark.py')
    torch.set_num_threads(min(4, torch.get_num_threads()))
    results, failures = [], []
    for path in candidates():
        if len(results) == 5:
            break
        print(f'Extracting F0 for clip {len(results)+1}/5: {path.name}...', flush=True)
        try:
            audio, sr = librosa.load(path, sr=16000, mono=True, duration=10)
            if len(audio) < sr or np.max(np.abs(audio)) < 1e-5:
                raise ValueError('Clip is shorter than one second or silent')
            with torch.inference_mode():
                f0, periodicity = torchcrepe.predict(torch.from_numpy(audio).unsqueeze(0), sr,
                    hop_length=160, fmin=50, fmax=550, model='tiny', batch_size=256,
                    device='cuda' if torch.cuda.is_available() else 'cpu', return_periodicity=True)
            f0, periodicity = f0.cpu().numpy()[0], periodicity.cpu().numpy()[0]
            energy = np.sqrt(np.mean(librosa.util.frame(np.pad(audio, (512, 512)), frame_length=1024, hop_length=160)**2, axis=0))
            world, times = pw.dio(audio.astype(np.float64), sr, f0_floor=50, f0_ceil=550, frame_period=10)
            world = pw.stonemask(audio.astype(np.float64), world, times, sr)
            n = min(len(f0), len(world), len(energy), int(np.ceil(len(audio)/160)))
            f0, world = f0[:n], world[:n]
            voiced = (periodicity[:n] >= 0.5) & (energy[:n] > 0.005)
            world_voiced = world > 0
            both = voiced & world_voiced
            count = int(both.sum())
            corr = float(np.corrcoef(f0[both], world[both])[0, 1]) if count > 1 and np.std(f0[both]) > 0 and np.std(world[both]) > 0 else None
            if corr is not None and not np.isfinite(corr):
                corr = None
            name = f'clip_{len(results)+1:02d}'
            save(OUT / f'{name}_torchcrepe.json', contour(f0, voiced))
            save(OUT / f'{name}_pyworld.json', contour(world, world_voiced))
            results.append({'clip': name, 'source': str(path.relative_to(ROOT)), 'offset_seconds': 0,
                'duration_seconds': len(audio)/sr, 'frames': n, 'jointly_voiced_frames': count,
                'correlation': corr, 'mae_hz': float(np.mean(np.abs(f0[both]-world[both]))) if count else None,
                'voicing_agreement': float(np.mean(voiced == world_voiced))})
            print(f'  Saved {n} frames; voiced overlap={count}, correlation={corr}', flush=True)
        except Exception as exc:
            failures.append({'source': str(path), 'error': str(exc)})
            print(f'  Failed: {exc}', flush=True)
            if len(failures) >= 20:
                break
    print('Saving results...', flush=True)
    save(OUT / 'pitch_comparison_summary.json', {'status': 'complete' if len(results) >= 3 else 'incomplete',
        'model': 'torchcrepe tiny', 'reference': 'PyWorld DIO + StoneMask', 'sample_rate': 16000,
        'hop_ms': 10, 'fmin_hz': 50, 'fmax_hz': 550, 'periodicity_threshold': 0.5,
        'rms_threshold': 0.005, 'comparison': 'Pearson correlation and MAE on jointly voiced aligned frames only',
        'selection_note': 'First ten seconds; Bengali identity inferred from dubbed filenames, not language detection.',
        'clips': results, 'failures': failures})
    print(f'Pitch benchmark finished: {len(results)} clips', flush=True)

if __name__ == '__main__':
    main()
