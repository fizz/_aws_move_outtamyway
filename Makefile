DATALAKE ?= datalake/aws_api_catalog
FPATH_DIR ?= ~/.zsh/completions
BASH_COMP_DIR ?= ~/.bash_completion.d

.PHONY: ingest generate install test changelog clean

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

# Release notes: what changed in the completer since the release PREV, for example
#   make changelog PREV=v0.1.0 > notes.md
changelog:
	@test -n "$(PREV)" || { echo "Set PREV to the previous release tag, for example PREV=v0.1.0." >&2; exit 1; }
	@mkdir -p .cache
	@git show $(PREV):_aws_move_outtamyway > .cache/previous_completer
	@python3 scripts/changelog.py .cache/previous_completer _aws_move_outtamyway --old-version $(PREV) --new-version "$(or $(VERSION),this release)"

clean:
	rm -rf datalake/ .cache/
