PYTHON ?= python
TEST_TOKEN_FILE ?= test.token
PROD_TOKEN_FILE ?= prod.token

.PHONY: dev prod build clean test

dev:
	@test -s $(TEST_TOKEN_FILE) || { echo "Missing or empty token file: $(TEST_TOKEN_FILE)"; exit 1; }
	$(PYTHON) publish.py --token-file $(TEST_TOKEN_FILE)

prod:
	@test -s $(PROD_TOKEN_FILE) || { echo "Missing or empty token file: $(PROD_TOKEN_FILE)"; exit 1; }
	$(PYTHON) publish.py --prod --token-file $(PROD_TOKEN_FILE)

build:
	$(PYTHON) publish.py --build

test:
	$(PYTHON) -m pytest

clean:
	rm -rf dist build src/*.egg-info *.egg-info
