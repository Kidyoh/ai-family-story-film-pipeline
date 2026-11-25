PY ?= .venv/bin/python
export PYTHONUNBUFFERED := 1

.PHONY: all film mock setup validate script narration keyframes shots music assemble clean outputs

all: film

## The one command. From a clean clone: make setup, then make film.
film: script narration keyframes shots music assemble

## Keyless, deterministic build. No network, no secrets, same file names.
mock:
	PROVIDER_MODE=mock $(MAKE) film

setup:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements.txt
	@echo "venv ready. ffmpeg: $$($(PY) -c 'import common_ffmpeg' 2>/dev/null || echo 'bundled with imageio-ffmpeg')"

validate:
	$(PY) -m pipeline.script --dry-run

script: validate
	$(PY) -m pipeline.script

narration:
	$(PY) -m pipeline.narration

keyframes:
	$(PY) -m pipeline.keyframes

shots:
	$(PY) -m pipeline.shots

music:
	$(PY) -m pipeline.music

assemble:
	$(PY) -m pipeline.assemble

outputs:
	@find outputs -maxdepth 2 -type f \( -name '*.mp4' -o -name '*.wav' -o -name '*.json' \) | sort

clean:
	rm -rf outputs
	: > runs.jsonl
