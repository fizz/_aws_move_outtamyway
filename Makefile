DATALAKE ?= datalake/aws_api_catalog
FPATH_DIR ?= ~/.zsh/completions

.PHONY: ingest generate install test clean

ingest:
	uv run python src/ingest.py --output $(DATALAKE)

generate: $(DATALAKE)
	uv run python src/generate.py --datalake $(DATALAKE) --output _aws_move_outtamyway

install: _aws_move_outtamyway
	mkdir -p $(FPATH_DIR)
	cp _aws_move_outtamyway $(FPATH_DIR)/
	@echo "Add to .zshrc (after compinit):"
	@echo "  fpath=($(FPATH_DIR) \$$fpath)"
	@echo "  compdef _aws_move_outtamyway aws"

test:
	uv run pytest tests/ -v
	zsh tests/test_completions.zsh

clean:
	rm -rf datalake/ .cache/
