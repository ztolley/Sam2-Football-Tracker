# SAM 2 Player Tracker

This folder contains an interactive single-player tracking workflow built on the `sam2` pip package.

Key files:

- `.venv/`: isolated Python environment
- `pyproject.toml`: root project metadata and Python dependencies
- `run_sam2_track.sh`: small wrapper with examples plus the Python CLI
- `samples/`: optional local sample videos for manual testing; ignored by Git
- `track_player_sam2.py`: prompt, review, correction, and render workflow

Setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Requirements:

- Python `>=3.10`
- `ffmpeg` on the system path
- the Python dependencies in `pyproject.toml`

Dependency notes:

- `sam2` is pinned to `1.1.0` in `pyproject.toml`
- `torch>=2.5.1` and `torchvision>=0.20.1` match the current SAM 2 package requirements
- the remaining packages are expressed as minimum versions rather than exact pins, so newer compatible releases may install

As of March 11, 2026, `sam2==1.1.0` is still the current PyPI release. The project keeps the SAM2 pin, but otherwise prefers minimum-version constraints so the environment can absorb routine upstream package updates.

Local-only folders:

- `debug/` is disposable output from ad hoc inspection/debugging and is ignored by Git.
- `samples/` can hold local test videos and is ignored by Git except for a placeholder file.

Typical usage:

```bash
./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47
```

Default workflow:

- draw the initial box on the requested frame
- wait while frame extraction, frame loading, tracking, and rendering progress are shown in the review window
- review the tracked video in an OpenCV window with a transport strip below the video
- use the play/pause button or `Space` to pause/resume
- click or drag the bottom timeline to scrub
- press `c` at a bad frame to redraw the player box there and recompute from that frame onward
- press `x` when the player leaves the frame so later frames are not highlighted until another box is added
- press `a` / `d` to step backward/forward while paused
- press `q` to accept and save the final render
- press `Esc` or close the window to quit without saving

For a non-interactive seeded run:

```bash
./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 box=321,339,365,417
```

If you want to skip the review window and provide correction prompts up front, add one or more later frames:

```bash
./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 select-frame=840 name=bm4-sam2-corrected
```

That command will ask you to draw a box on frame 47 and again on frame 840. Use additional `select-frame=` arguments for more corrections if needed.

Player label example:

```bash
./run_sam2_track.sh source=~/Movies/football/bm4.mp4 frame-idx=47 player-name="QB 12"
```

Current output behavior:

- the final MP4 uses a box-only highlight plus the player label
- the editing transport controls are not written into the exported video
- if the player is marked off-screen, the output simply stops highlighting them until a later corrective box is added

Wrapper help:

```bash
./run_sam2_track.sh --wrapper-help
./run_sam2_track.sh --help
```
