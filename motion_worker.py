#!/usr/bin/env python3
"""Generate short background clips with a local video model. Run by motion.py.

This is the only file that imports the video stack (torch, diffusers), and it
runs in its own environment (motion.VENV) so those packages never touch the
interpreter the rest of the pipeline uses. It is handed a JSON job file and
writes one MP4 per job:

    {"model": "Wan-AI/...", "width": 704, "height": 1280, "frames": 97,
     "fps": 24, "steps": 40,
     "jobs": [{"prompt": "...", "negative": "...", "seed": 123, "out": "x.mp4"}]}

The model loads once per invocation and serves every job in the file.
Model: Wan 2.2 TI2V 5B (Apache-2.0, local) - 720p at 24 fps; see motion.MODELS.
"""
import json, sys, time

def main(job_file):
    import numpy as np
    import torch
    import imageio_ffmpeg
    from diffusers import AutoencoderKLWan, WanPipeline

    with open(job_file, encoding="utf-8") as f:
        spec = json.load(f)
    w, h, n, fps = spec["width"], spec["height"], spec["frames"], spec["fps"]

    t0 = time.time()
    vae = AutoencoderKLWan.from_pretrained(spec["model"], subfolder="vae",
                                           torch_dtype=torch.float32)
    pipe = WanPipeline.from_pretrained(spec["model"], vae=vae,
                                       torch_dtype=torch.bfloat16)
    # Offload keeps peak VRAM under 24GB with the large text encoder in play;
    # the encoder only runs once per clip, so it costs seconds.
    pipe.enable_model_cpu_offload()
    print(f"model {spec['model']} loaded in {time.time() - t0:.0f}s", flush=True)

    ok = 0
    for job in spec["jobs"]:
        t = time.time()
        try:
            video = pipe(
                prompt=job["prompt"], negative_prompt=job.get("negative", ""),
                height=h, width=w, num_frames=n,
                guidance_scale=5.0,
                num_inference_steps=job.get("steps", spec["steps"]),
                generator=torch.Generator("cpu").manual_seed(int(job["seed"])),
                output_type="np",
            ).frames[0]
            frames = (np.clip(video, 0, 1) * 255).astype("uint8")
            writer = imageio_ffmpeg.write_frames(
                job["out"], (w, h), fps=fps, codec="libx264",
                quality=8, macro_block_size=16,
                output_params=["-pix_fmt", "yuv420p"])
            writer.send(None)
            for fr in frames:
                writer.send(np.ascontiguousarray(fr))
            writer.close()
            ok += 1
            print(f"ok {job['out']} {time.time() - t:.0f}s", flush=True)
        except Exception as e:                              # noqa: BLE001
            # One clip failing costs that clip; the post keeps a still cover.
            print(f"fail {job['out']} {type(e).__name__}: {e}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
