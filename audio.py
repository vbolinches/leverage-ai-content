#!/usr/bin/env python3
"""Generate an original music bed for a Reel.

Why synthesise instead of using a track:

Instagram's Content Publishing API cannot attach trending or licensed audio to
an API-published Reel — there is no audio_id parameter, and Meta does not expose
the music library to third parties. Audio must be embedded in the MP4 before
upload. That leaves two options: ship a licensed file (copyright risk, and
Instagram mutes or strikes what it detects), or generate something original.

This generates it. Every sample is computed here, so the audio is original by
construction: no licence, no attribution, no takedown surface.

The bed is deliberately plain — a slow minor loop under a talking-head-less
slideshow. It exists so the Reel is not silent (silent Reels get suppressed) and
so slide changes land on an audible cue, which helps retention. It is not trying
to be the reason someone watches.

Seeded from the post slug, so each Reel differs but the same post always renders
the same audio — reruns stay reproducible and diffs stay clean.

    python audio.py bed.wav --seconds 14 --slug post02-meeting-notes-prompt
"""
import argparse, hashlib, wave
import numpy as np

SR = 44100
BPM = 84.0
BEAT = 60.0 / BPM
BAR = BEAT * 4

# Minor triads relative to the root, as semitone offsets: i - VI - III - VII.
# A common, unobtrusive loop; resolves without demanding attention.
PROGRESSION = [(0, 3, 7), (8, 12, 15), (3, 7, 10), (10, 14, 17)]

# Roots are picked per-slug from this set. All land in a low-mid register that
# sits under speech-free video without becoming muddy on a phone speaker.
ROOTS = [53, 55, 56, 57, 58, 60]  # F3, G3, G#3, A3, A#3, C4

MIX = {"pad": 0.30, "sub": 0.24, "kick": 0.55, "hat": 0.09,
       "pluck": 0.17, "accent": 0.22}


def _midi(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def _seed(slug):
    return int(hashlib.sha256(slug.encode()).hexdigest()[:8], 16)


def _lowpass(x, cutoff, order=2):
    """Zero-phase Butterworth magnitude rolloff, done in the frequency domain.

    FFT rather than an IIR loop: numpy-only (no scipy in CI) and vectorised.
    """
    n = len(x)
    if n == 0:
        return x
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    spec *= 1.0 / np.sqrt(1.0 + (f / cutoff) ** (2 * order))
    return np.fft.irfft(spec, n)


def _highpass(x, cutoff, order=2):
    n = len(x)
    if n == 0:
        return x
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    with np.errstate(divide="ignore"):
        ratio = np.where(f > 0, cutoff / np.maximum(f, 1e-9), 1e9)
    spec *= 1.0 / np.sqrt(1.0 + ratio ** (2 * order))
    return np.fft.irfft(spec, n)


def _tone(freq, n, harmonics=6, detune=0.004):
    """Additive voice: a few harmonics, plus a detuned copy for width."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    norm = 0.0
    for h in range(1, harmonics + 1):
        amp = 1.0 / (h ** 1.4)
        out += amp * np.sin(2 * np.pi * freq * h * t)
        out += amp * np.sin(2 * np.pi * freq * h * (1 + detune) * t)
        norm += 2 * amp
    return out / norm


def _env(n, attack, decay, sustain, release):
    """Simple ADSR over n samples; times in seconds, sustain is a level."""
    a, d, r = int(attack * SR), int(decay * SR), int(release * SR)
    a, d, r = min(a, n), min(d, n), min(r, n)
    s = max(n - a - d - r, 0)
    return np.concatenate([
        np.linspace(0, 1, a, endpoint=False),
        np.linspace(1, sustain, d, endpoint=False),
        np.full(s, sustain),
        np.linspace(sustain, 0, r),
    ])[:n].astype(np.float64)


def _add(buf, sig, at):
    """Mix sig into buf at sample offset `at`, clipping to the buffer end."""
    i = max(int(at), 0)
    if i >= len(buf):
        return
    seg = sig[:len(buf) - i]
    buf[i:i + len(seg)] += seg


def _kick(n_total, at):
    """Sine pitched from 110Hz to 45Hz — a soft thud, no click."""
    n = int(0.28 * SR)
    t = np.arange(n) / SR
    f = 45 + 65 * np.exp(-t / 0.035)
    phase = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(phase) * np.exp(-t / 0.09)


def _hat(rng):
    n = int(0.06 * SR)
    t = np.arange(n) / SR
    return _highpass(rng.normal(0, 1, n), 7000) * np.exp(-t / 0.014)


def _pluck(freq):
    n = int(0.5 * SR)
    t = np.arange(n) / SR
    sig = _tone(freq, n, harmonics=4, detune=0.002) * np.exp(-t / 0.13)
    return _lowpass(sig, 3500)


def _accent(rng):
    """A soft tick + upward blip, placed on video slide changes."""
    n = int(0.22 * SR)
    t = np.arange(n) / SR
    tick = _highpass(rng.normal(0, 1, n), 4000) * np.exp(-t / 0.03)
    blip = np.sin(2 * np.pi * (600 + 900 * t / 0.22) * t) * np.exp(-t / 0.05)
    return _lowpass(tick * 0.7 + blip * 0.5, 9000)


def render_bed(duration, slug, accents=()):
    """Return an (n, 2) float32 stereo bed of exactly `duration` seconds.

    `accents` are times in seconds (video slide changes) that get an audible cue.
    """
    rng = np.random.default_rng(_seed(slug))
    root = ROOTS[_seed(slug) % len(ROOTS)]
    n = int(duration * SR)
    left, right = np.zeros(n), np.zeros(n)

    bars = int(np.ceil(duration / BAR)) + 1
    for b in range(bars):
        chord = PROGRESSION[b % len(PROGRESSION)]
        start = b * BAR

        # Pad: the triad, held across the bar with a slow swell.
        seg_n = int((BAR + 0.6) * SR)
        env = _env(seg_n, 0.35, 0.25, 0.75, 0.5)
        for j, semis in enumerate(chord):
            voice = _tone(_midi(root + semis), seg_n) * env * MIX["pad"] / 3
            voice = _lowpass(voice, 2200)
            # Spread the triad across the stereo field.
            pan = 0.5 + (j - 1) * 0.22
            _add(left, voice * (1 - pan), start * SR)
            _add(right, voice * pan, start * SR)

        # Sub: the root, two octaves down, one note per bar.
        sub_n = int(BAR * 0.9 * SR)
        sub = (np.sin(2 * np.pi * _midi(root - 24) * np.arange(sub_n) / SR)
               * _env(sub_n, 0.02, 0.15, 0.6, 0.4) * MIX["sub"])
        _add(left, sub, start * SR)
        _add(right, sub, start * SR)

        # Drums: kick on 1 and 3, hats on the offbeats.
        for beat in (0, 2):
            k = _kick(n, 0) * MIX["kick"]
            _add(left, k, (start + beat * BEAT) * SR)
            _add(right, k, (start + beat * BEAT) * SR)
        for beat in (0.5, 1.5, 2.5, 3.5):
            h = _hat(rng) * MIX["hat"]
            _add(left, h * 1.15, (start + beat * BEAT) * SR)
            _add(right, h * 0.85, (start + beat * BEAT) * SR)

        # Pluck: one chord tone per bar, alternating sides. Adds movement
        # without introducing a melody that competes with on-screen text.
        note = chord[rng.integers(0, len(chord))] + 12
        p = _pluck(_midi(root + note)) * MIX["pluck"]
        side = b % 2
        _add(left, p * (0.65 if side else 0.35), (start + 3 * BEAT) * SR)
        _add(right, p * (0.35 if side else 0.65), (start + 3 * BEAT) * SR)

    # Slide-change cues. Off the musical grid on purpose — they mark the video,
    # not the music, and stay quiet enough to read as texture.
    for t in accents:
        a = _accent(rng) * MIX["accent"]
        _add(left, a, t * SR)
        _add(right, a, t * SR)

    stereo = np.stack([left[:n], right[:n]], axis=1)

    # Soft-clip rather than hard-limit, then normalise. Instagram re-normalises
    # loudness on its side, so headroom matters more than absolute level.
    stereo = np.tanh(stereo * 1.4) / 1.4
    peak = np.abs(stereo).max()
    if peak > 0:
        stereo *= 0.72 / peak

    # Short fade-in so the first kick lands immediately (the opening second is
    # the one that decides retention); longer fade-out so the end isn't abrupt.
    fi, fo = int(0.15 * SR), min(int(0.8 * SR), n)
    stereo[:fi] *= np.linspace(0, 1, fi)[:, None]
    stereo[n - fo:] *= np.linspace(1, 0, fo)[:, None]
    return stereo.astype(np.float32)


def write_wav(path, stereo):
    pcm = np.clip(stereo, -1.0, 1.0)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=14.0)
    ap.add_argument("--slug", default="preview")
    a = ap.parse_args()
    write_wav(a.out, render_bed(a.seconds, a.slug))
    print(f"{a.seconds:.1f}s bed for {a.slug!r} -> {a.out}")


if __name__ == "__main__":
    main()
