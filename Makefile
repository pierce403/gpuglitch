PYTHON ?= python3
BOARD ?= pico2_w
BUILD_DIR ?= build/$(BOARD)
DEMO_DB ?= build/demo.sqlite3
BUILD_DB ?= build/firmware-history.sqlite3
.PHONY: test doctor demo install firmware

test:
	$(PYTHON) -m unittest discover -s tests -v

doctor:
	$(PYTHON) -m ga100ctl doctor

demo:
	$(PYTHON) -m ga100ctl --simulate --db "$(DEMO_DB)" gpu inspect --gpu 0000:41:00.0
	$(PYTHON) -m ga100ctl --simulate --db "$(DEMO_DB)" report

install:
	$(PYTHON) -m pip install .

firmware:
	$(PYTHON) -m ga100ctl --db "$(BUILD_DB)" pico build --board "$(BOARD)" --sdk "$(PICO_SDK_PATH)" --build-dir "$(BUILD_DIR)"
