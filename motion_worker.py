#!/usr/bin/env python3
"""Generate short background clips with a local video model. Run by motion.py.

This is the only file that imports the video stack (torch, diffusers), and it
runs in its own environment (.venv-motion, see motion.py) so those packages
never touch the interpreter the rest of the pipeline uses. It is handed a JSON
job file and writes one MP4 per job:

    [{"prompt": "...", "negative": "...", "seed": 123, "out": "path.mp4"}]

The model loads once per invocation and serves every job in the file.

Model: Wan 2.1 text-to-video, 1.3B, Apache-2.0 - the smallest model that makes
usable motion, and a licence that allows commercial use without conditions.
Portrait 480x832, 65 frames at 16 fps: about four seconds, which is the whole
life of a cover. render_reel upscales it; as a dimmed background it loses
nothing to that.
"""
import json, sys, time

MODEL = "Wan-AI/Wan2.1-T2V-1.3B-Diffusers"
WIDTH, HEIGHT, FRAMES, FPS = 480, 832, 65, 16


def main(job_file):
    import numpy as np
    import torch
    import imageio_ffmpeg
    from diffusers import AutoencoderKLWan, WanPipeline

    with open(job_file, encoding="utf-8") as f:
        jobs = json.load(f)

    t0 = time.time()
    vae = AutoencoderKLWan.from_pretrained(MODEL, subfolder="vae",
                                           torch_dtype=torch.float32)
    pipe = WanPipeline.from_pretrained(MODEL, vae=vae, torch_dtype=torch.bfloat16)
    # Offload keeps peak VRAM well under 24GB with the 11GB text encoder in
    # play; the encoder only runs once per clip, so it costs seconds.
    pipe.enable_model_cpu_offload()
    print(f"model loaded in {time.time() - t0:.0f}s", flush=True)

    ok = 0
    for job in jobs:
        t = time.time()
        try:
            video = pipe(
                prompt=job["prompt"], negative_prompt=job.get("negative", ""),
                height=HEIGHT, width=WIDTH, num_frames=FRAMES,
                guidance_scale=5.0, num_inference_steps=job.get("steps", 30),
                generator=torch.Generator("cpu").manual_seed(int(job["seed"])),
                output_type="np",
            ).frames[0]
            frames = (np.clip(video, 0, 1) * 255).astype("uint8")
            w = imageio_ffmpeg.write_frames(
                job["out"], (WIDTH, HEIGHT), fps=FPS, codec="libx264",
                quality=8, macro_block_size=16,
                output_params=["-pix_fmt", "yuv420p"])
            w.send(None)
            for fr in frames:
                w.send(np.ascontiguousarray(fr))
            w.close()
            ok += 1
            print(f"ok {job['out']} {time.time() - t:.0f}s", flush=True)
        except Exception as e:                              # noqa: BLE001
            # One clip failing costs that clip; the post keeps a still cover.
            print(f"fail {job['out']} {type(e).__name__}: {e}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
