import pytest

from hermes_mempalace_routing.config import RoutingConfig


def test_invalid_storage_backend(tmp_path):
    cfg = RoutingConfig(base_dir=tmp_path)
    object.__setattr__(cfg, "storage_backend", "postgres")  # bypass Literal for negative test
    with pytest.raises(ValueError, match="storage_backend"):
        cfg.validate()


def test_invalid_tokenizer_strategy(tmp_path):
    cfg = RoutingConfig(base_dir=tmp_path)
    object.__setattr__(cfg, "tokenizer_strategy", "unknown")
    with pytest.raises(ValueError, match="tokenizer_strategy"):
        cfg.validate()


def test_db_path_directory_rejected(tmp_path):
    bad = tmp_path / "is_dir"
    bad.mkdir()
    with pytest.raises(ValueError, match="not a directory"):
        RoutingConfig(base_dir=tmp_path, storage_backend="sqlite", db_path=bad).validate()


def test_redact_without_write_rejected(tmp_path):
    with pytest.raises(ValueError, match="redact"):
        RoutingConfig(
            base_dir=tmp_path,
            write_raw_artifacts=False,
            redact_before_persist=True,
        ).validate()


def test_route_score_threshold_range(tmp_path):
    with pytest.raises(ValueError, match="route_score_threshold"):
        RoutingConfig(base_dir=tmp_path, route_score_threshold=1.5).validate()
