import json

import pytest

from performance.targets.echomem.probes import docker_inspect


def test_resource_socket_uses_explicit_colima_host(monkeypatch):
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/colima/docker.sock")
    assert docker_inspect._docker_socket_path() == "/tmp/colima/docker.sock"


def test_resource_socket_uses_selected_context(monkeypatch):
    monkeypatch.setenv("DOCKER_CONTEXT", "dedicated-stress")
    monkeypatch.setenv("DOCKER_HOST", "unix:///tmp/not-selected.sock")
    monkeypatch.setattr(docker_inspect.shutil, "which", lambda name: "/bin/docker")
    def inspect(command, **kwargs):
        assert command == ["docker", "context", "inspect"]
        return json.dumps([{"Endpoints": {"docker": {"Host": "unix:///tmp/selected.sock"}}}])
    monkeypatch.setattr(docker_inspect.subprocess, "check_output", inspect)
    assert docker_inspect._docker_socket_path() == "/tmp/selected.sock"


def test_resource_socket_never_falls_back_from_remote_host(monkeypatch):
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote.example:2375")
    with pytest.raises(ValueError, match="selected Docker Unix socket"):
        docker_inspect._docker_socket_path()


def test_default_socket_without_docker_cli(monkeypatch):
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(docker_inspect.shutil, "which", lambda name: None)
    assert docker_inspect._docker_socket_path() == "/var/run/docker.sock"
