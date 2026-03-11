# SAM 2 Player Tracker

This folder contains a promptable single-player tracking workflow built on the `sam2` pip package.

Key files:

- `.venv/`: isolated Python environment
- `pyproject.toml`: root project metadata and Python dependencies
- `run_sam2_track.sh`: wrapper to run the local tracking script
- `samples/`: optional local sample videos for manual testing; ignored by Git
- `track_player_sam2.py`: box-prompted single-player tracker for videos

Setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Python dependencies are resolved from `pyproject.toml`, including a pinned `sam2==1.1.0`.

Non-Python dependency:

- `ffmpeg` must be installed on your system so frames can be extracted from source videos.

Local-only folders:

- `debug/` is disposable output from ad hoc inspection/debugging and is ignored by Git.
- `samples/` can hold local test videos and is ignored by Git except for a placeholder file.

Typical usage:

```bash
./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47
```

Default workflow:

- draw the initial box on the requested frame
- wait for SAM 2 to compute
- review the tracked video in a window with time and frame number shown
- press `Space` to pause/resume
- press `c` at a bad frame to redraw the player box there and recompute
- press `a` / `d` to step backward/forward while paused
- press `q` or `Esc` when you are done correcting and want the final render written

For a non-interactive seeded run:

```bash
./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 box=321,339,365,417
```

If you want to skip the review window and provide correction prompts up front, add one or more later frames:

```bash
./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 select-frame=840 name=bm4-sam2-corrected
```

That command will ask you to draw a box on frame 47 and again on frame 840. Use additional `select-frame=` arguments for more corrections if needed.
