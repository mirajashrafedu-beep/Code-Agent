.PHONY: help up down build restart logs migrate seed clean test

help:
	@echo "Enterprise LLM Agent Platform - Management Commands"
	@echo "make up        - Start all services with Docker Compose"
	@echo "make down      - Stop all services"
	@echo "make build     - Rebuild docker containers"
	@echo "make restart   - Restart all containers"
	@echo "make logs      - Tail logs from all containers"
	@echo "make migrate   - Run Alembic relational migrations"
	@echo "make seed      - Populate seed users and initial prompt templates"
	@echo "make test      - Run backend unit and integration test suite"
	@echo "make clean     - Stop and remove dangling docker volumes"

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

restart:
	docker compose down && docker compose up -d

logs:
	docker compose logs -f

migrate:
	docker compose exec backend alembic upgrade head

seed:
	docker compose exec backend python -m src.scripts.seed

test:
	docker compose exec backend pytest -v

clean:
	docker compose down -v --remove-orphans
