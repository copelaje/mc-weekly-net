COMPOSE = docker compose --profile relay

launch:
	$(COMPOSE) up --build -d

logs:
	$(COMPOSE) logs -f

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

.PHONY: launch logs down restart
