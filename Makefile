BOOKSIM_DIR := third_party/booksim2/src
BOOKSIM     := $(BOOKSIM_DIR)/booksim
PYTHON      ?= python3

.PHONY: all booksim topologies simulate boardfly clean help

all: booksim topologies

help:
	@echo "make booksim      — собрать BookSim 2.0"
	@echo "make topologies   — сгенерировать anynet-файлы (K_n и циркулянты)"
	@echo "make simulate     — прогнать сравнение полносвязной и циркулянтных сетей"
	@echo "make smoke        — короткий прогон штатного mesh-примера BookSim"
	@echo "make boardfly     — модель Boardfly 4×8×36 и сравнение с тором 8×8×16"
	@echo "make clean        — удалить сборку BookSim и сгенерированные прогоны"

booksim: $(BOOKSIM)

$(BOOKSIM):
	$(MAKE) -C $(BOOKSIM_DIR)

topologies:
	$(PYTHON) scripts/generate_topologies.py

simulate: booksim topologies
	$(PYTHON) scripts/run_simulations.py

boardfly: booksim
	$(PYTHON) scripts/boardfly_topology.py
	$(PYTHON) scripts/run_boardfly.py

smoke: booksim
	$(BOOKSIM) third_party/booksim2/runfiles/meshconfig \
		k=4 n=2 injection_rate=0.10 sample_period=500 \
		warmup_periods=1 max_samples=3 sim_count=1

clean:
	$(MAKE) -C $(BOOKSIM_DIR) clean
	rm -rf experiments/runs
