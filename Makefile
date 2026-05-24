.PHONY: broker stop logs test

broker:
	docker compose up -d

stop:
	docker compose down

logs:
	docker compose logs -f

test:
	python -m pytest tests/tests.py -v