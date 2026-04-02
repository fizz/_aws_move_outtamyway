DATALAKE ?= datalake/aws_api_catalog
FPATH_DIR ?= ~/.zsh/completions
BASH_COMP_DIR ?= ~/.bash_completion.d

.PHONY: ingest generate install test clean

ingest:
	uv run python src/ingest.py --output $(DATALAKE)

generate: $(DATALAKE)
	uv run python src/generate.py --datalake $(DATALAKE) --output _aws_move_outtamyway --output-bash _aws_move_outtamyway.bash

install: _aws_move_outtamyway _aws_move_outtamyway.bash
	mkdir -p $(FPATH_DIR)
	cp _aws_move_outtamyway $(FPATH_DIR)/
	mkdir -p $(BASH_COMP_DIR)
	cp _aws_move_outtamyway.bash $(BASH_COMP_DIR)/
	@echo "Zsh: add to .zshrc (after compinit):"
	@echo "  fpath=($(FPATH_DIR) \$$fpath)"
	@echo "  compdef _aws_move_outtamyway aws"
	@echo ""
	@echo "Bash: add to .bashrc:"
	@echo "  source $(BASH_COMP_DIR)/_aws_move_outtamyway.bash"

test:
	uv run pytest tests/ -v
	zsh tests/test_completions.zsh

clean:
	rm -rf datalake/ .cache/
