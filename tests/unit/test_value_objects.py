from pathlib import Path


class TestConfig:
    def test_create_config(self, sample_config):
        assert sample_config.difficulty == "medium"
        assert sample_config.quantity == "standard"
        assert sample_config.timeout == 900

    def test_config_paths_exist(self, sample_config):
        assert sample_config.input_dir.exists()
        assert sample_config.output_dir.exists()


class TestSourceInfo:
    def test_create_source_info(self, sample_source_info):
        assert sample_source_info.source_id == "src123"
        assert sample_source_info.status == "ready"
        assert isinstance(sample_source_info.file_path, Path)
