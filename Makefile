DATALAKE ?= datalake/aws_api_catalog
# VERSION: an AWS CLI v2 release, for example VERSION=2.39.3. Default: the newest.
FPATH_DIR ?= ~/.zsh/completions
BASH_COMP_DIR ?= ~/.bash_completion.d

.PHONY: ingest generate install test changelog clean

ingest:
	uv run python src/ingest.py --output $(DATALAKE) $(if $(VERSION),--aws-cli-version $(VERSION))

# The completers are generated, not committed: every version is on the branch history
# and attached to its release.
$(DATALAKE):
	$(MAKE) ingest

generate: | $(DATALAKE)
	uv run python src/generate.py --datalake $(DATALAKE) --output _aws_move_outtamyway --output-bash _aws_move_outtamyway.bash $(if $(VERSION),--aws-cli-version $(VERSION))

_aws_move_outtamyway _aws_move_outtamyway.bash:
	$(MAKE) generate

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

test: _aws_move_outtamyway
	uv run pytest tests/ -v
	zsh tests/test_completions.zsh

# Release notes between two releases on the branch history, for example
#   make changelog PREV=2.39.2 VERSION=2.39.3 > notes.md
changelog:
	@test -n "$(PREV)" -a -n "$(VERSION)" || { echo "Set PREV and VERSION to two release tags, for example PREV=2.39.2 VERSION=2.39.3." >&2; exit 1; }
	@mkdir -p .cache
	@git show $(PREV):_aws_move_outtamyway > .cache/previous_completer
	@git show $(VERSION):_aws_move_outtamyway > .cache/completer
	@python3 scripts/changelog.py .cache/previous_completer .cache/completer --old-version $(PREV) --new-version $(VERSION)

clean:
	rm -rf datalake/ .cache/ _aws_move_outtamyway _aws_move_outtamyway.bash
