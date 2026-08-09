from xe_lang.helper import Result
from xe_lang.vm import MAGIC, VERSION, VM


def _vm(tmp_path) -> VM:
	return VM([MAGIC, VERSION, 0, 0], filesystem_root=tmp_path)


def test_execute_reuses_the_supplied_result_on_success(tmp_path) -> None:
	vm = _vm(tmp_path)
	shared = Result()

	returned = vm.execute(42, shared)

	assert returned is shared
	assert returned.error is None
	assert returned.value is True
	assert vm.stack[:vm.sp] == [42]


def test_execute_reuses_the_supplied_result_on_stack_error(tmp_path) -> None:
	vm = _vm(tmp_path)
	shared = Result()
	pop_one = (1 << 32) | (2 << 16) | 1

	returned = vm.execute(pop_one, shared)

	assert returned is shared
	assert returned.error is not None
	assert returned.value is None
