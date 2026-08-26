import pytest

from primesim_bridge.models import EXIT_CODE_TABLE, ExecutionStatus, classify_exit


ALL_DOCUMENTED_CODES = list(range(0, 26)) + list(range(28, 35))


@pytest.mark.parametrize("returncode", ALL_DOCUMENTED_CODES)
def test_classify_every_documented_exit_code(returncode):
    status, message = classify_exit(returncode)
    if returncode == 0:
        assert status is ExecutionStatus.SUCCESS
        assert message is None
    else:
        assert status is ExecutionStatus.FAILURE
        assert message == EXIT_CODE_TABLE[returncode]


def test_exit_code_table_is_complete():
    assert set(EXIT_CODE_TABLE) == set(range(0, 26)) | set(range(28, 35))


def test_classify_unlisted_exit_code():
    assert classify_exit(27) == (ExecutionStatus.FAILURE, "exit code 27")
