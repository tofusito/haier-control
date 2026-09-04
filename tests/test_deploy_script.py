from pathlib import Path


def test_deploy_inventory_handles_containers_without_healthcheck() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "deploy-homelab.sh").read_text()

    assert 'if index .State "Health"' in script
    assert "if .State.Health" not in script
