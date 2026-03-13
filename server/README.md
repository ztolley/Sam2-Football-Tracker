# Server

This package contains:

- the FastAPI application scaffold
- the current interactive SAM2 tracking CLI
- Python tooling for linting, formatting, and tests

## Local setup

```bash
../scripts/bootstrap-server.sh
cp .env.example .env
../scripts/dev-server.sh
```

## CLI

```bash
../scripts/track-local.sh source=~/Movies/football/bm4.mp4 frame-idx=47
```
