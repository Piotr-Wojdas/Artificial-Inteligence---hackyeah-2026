import os

from solary.env import find_env_file, load_env


def test_env_file_is_found_in_a_parent_folder(tmp_path):
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert find_env_file(deep) == tmp_path / ".env"
    assert find_env_file(tmp_path) == tmp_path / ".env"


def test_values_are_loaded_but_never_override_the_environment(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "SOLARY_TEST_PLAIN=abc\n"
        'SOLARY_TEST_QUOTED="with spaces"\n'
        "export SOLARY_TEST_EXPORT='x=y'\n"
        "SOLARY_TEST_EMPTY=\n"
        "SOLARY_TEST_SET=from-file\n"
        "not a setting\n", encoding="utf-8")
    for name in ("PLAIN", "QUOTED", "EXPORT", "EMPTY"):
        monkeypatch.delenv(f"SOLARY_TEST_{name}", raising=False)
    monkeypatch.setenv("SOLARY_TEST_SET", "from-environment")

    assert load_env(tmp_path) == tmp_path / ".env"
    try:
        assert os.environ["SOLARY_TEST_PLAIN"] == "abc"
        assert os.environ["SOLARY_TEST_QUOTED"] == "with spaces"
        assert os.environ["SOLARY_TEST_EXPORT"] == "x=y"
        assert "SOLARY_TEST_EMPTY" not in os.environ
        assert os.environ["SOLARY_TEST_SET"] == "from-environment"
    finally:
        for name in ("PLAIN", "QUOTED", "EXPORT"):
            os.environ.pop(f"SOLARY_TEST_{name}", None)


def test_settings_from_the_environment_reach_the_config(tmp_path, monkeypatch):
    from solary.config import Config
    from solary.geocode import user_agent

    monkeypatch.setenv("SOLARY_DATA", str(tmp_path / "cache"))
    monkeypatch.setenv("SOLARY_USER_AGENT", "my-app/1.0 (me@example.com)")
    cfg = Config()
    assert cfg.data_dir == tmp_path / "cache" and cfg.roof_dir == tmp_path / "cache" / "roof"
    assert user_agent() == "my-app/1.0 (me@example.com)"
