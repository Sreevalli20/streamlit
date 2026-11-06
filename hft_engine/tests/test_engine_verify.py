import pytest

from bench import engine_verify


def test_verification_command_returns_failure_status_on_failed_check(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        engine_verify, "test_basic_match",
        lambda: engine_verify.check("deliberately failing regression", False),
    )
    for name in (
        "test_no_match",
        "test_partial_fill",
        "test_price_time_priority",
        "test_price_priority",
        "test_multi_sweep",
        "test_latency_hot_path",
    ):
        monkeypatch.setattr(engine_verify, name, lambda: None)

    with pytest.raises(SystemExit) as error:
        engine_verify.main()

    assert error.value.code == 1
    assert "deliberately failing regression" in capsys.readouterr().out
