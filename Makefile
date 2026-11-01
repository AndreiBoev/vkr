BOOKSIM_DIR := third_party/booksim2/src
BOOKSIM     := $(BOOKSIM_DIR)/booksim
PYTHON      ?= python3

.PHONY: all booksim run clean

all: run

booksim: $(BOOKSIM)

$(BOOKSIM):
	$(MAKE) -C $(BOOKSIM_DIR)

run: booksim
	$(PYTHON) scripts/boardfly.py

clean:
	$(MAKE) -C $(BOOKSIM_DIR) clean
	rm -rf experiments
