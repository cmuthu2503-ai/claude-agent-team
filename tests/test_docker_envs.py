"""P8-T09/T10: Docker environment tests — validates compose files and structure."""

import pytest
import yaml
from pathlib import Path


COMPOSE_FILES = [
    "docker-compose.yml",
    "docker-compose.staging.yml",
    "docker-compose.prod.yml",
    "docker-compose.demo.yml",
]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_compose_file_exists(compose_file):
    assert Path(compose_file).exists(), f"{compose_file} not found"


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_compose_file_valid_yaml(compose_file):
    with open(compose_file) as f:
        config = yaml.safe_load(f)
    assert "services" in config
    assert "backend" in config["services"]
    assert "frontend" in config["services"]


def test_dev_ports():
    with open("docker-compose.yml") as f:
        config = yaml.safe_load(f)
    backend_ports = config["services"]["backend"]["ports"]
    frontend_ports = config["services"]["frontend"]["ports"]
    assert "8000:8000" in backend_ports
    assert "3000:3000" in frontend_ports


def test_staging_ports():
    with open("docker-compose.staging.yml") as f:
        config = yaml.safe_load(f)
    assert "8010:8000" in config["services"]["backend"]["ports"]
    assert "3010:3000" in config["services"]["frontend"]["ports"]


def test_prod_ports():
    with open("docker-compose.prod.yml") as f:
        config = yaml.safe_load(f)
    assert "8020:8000" in config["services"]["backend"]["ports"]
    assert "3020:3000" in config["services"]["frontend"]["ports"]


def test_demo_ports():
    with open("docker-compose.demo.yml") as f:
        config = yaml.safe_load(f)
    assert "8030:8000" in config["services"]["backend"]["ports"]
    assert "3030:3000" in config["services"]["frontend"]["ports"]


def test_networks_isolated():
    """Each environment should have its own isolated network."""
    networks = set()
    for cf in COMPOSE_FILES:
        with open(cf) as f:
            config = yaml.safe_load(f)
        for net_name in config.get("networks", {}).keys():
            assert net_name not in networks, f"Network {net_name} reused across compose files"
            networks.add(net_name)


def test_prod_has_restart_policy():
    with open("docker-compose.prod.yml") as f:
        config = yaml.safe_load(f)
    for service in config["services"].values():
        assert service.get("restart") == "unless-stopped"


def test_prod_has_resource_limits():
    with open("docker-compose.prod.yml") as f:
        config = yaml.safe_load(f)
    backend = config["services"]["backend"]
    assert "deploy" in backend
    assert "resources" in backend["deploy"]
    assert "limits" in backend["deploy"]["resources"]


def test_prod_has_secrets():
    with open("docker-compose.prod.yml") as f:
        config = yaml.safe_load(f)
    assert "secrets" in config
    assert "anthropic_api_key" in config["secrets"]
    assert "jwt_secret" in config["secrets"]


def test_all_backend_services_have_healthcheck():
    for cf in COMPOSE_FILES:
        with open(cf) as f:
            config = yaml.safe_load(f)
        backend = config["services"]["backend"]
        assert "healthcheck" in backend, f"{cf} backend missing healthcheck"


def test_dockerfiles_exist():
    assert Path("Dockerfile.backend").exists()
    assert Path("Dockerfile.frontend").exists()


def test_makefile_has_all_targets():
    makefile = Path("Makefile").read_text()
    for target in ["dev", "down", "build", "staging", "prod", "demo", "down-all", "status", "health"]:
        assert f"{target}:" in makefile, f"Makefile missing target: {target}"
