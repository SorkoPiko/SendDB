previous_levels = []
previous_rated_levels = []

def test_send_results(levels: list[int], rated_levels: list[int]) -> tuple[list[int], list[int]]:
	global previous_levels, previous_rated_levels

	filtered_levels = [level for level in levels if level not in rated_levels]

	if not previous_levels:
		previous_levels = levels.copy()
		previous_rated_levels = rated_levels.copy()
		return filtered_levels.copy(), rated_levels.copy()

	sends = []
	rates = [level for level in rated_levels if level not in previous_rated_levels]

	prev_levels_working = previous_levels.copy()
	ignore_count = 0

	for level in rates:
		if level in prev_levels_working:
			prev_levels_working.remove(level)
			ignore_count += 1

	check_limit = len(filtered_levels) - ignore_count

	for i in range(check_limit):
		level = filtered_levels[i]

		prev_index = prev_levels_working.index(level) if level in prev_levels_working else float('inf')

		if i < prev_index:
			sends.append(level)

	sends.reverse()

	previous_levels = levels.copy()
	previous_rated_levels = rated_levels.copy()

	return sends, rates

def assert_test(input, expected):
	result = test_send_results(input[0], input[1])
	assert result == expected, f"Expected {expected}, got {result}"

assert_test(([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]), ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]))
assert_test(([21, 1, 2, 3, 4, 5, 6, 7, 8, 9], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]), ([21], []))
assert_test(([23, 22, 21, 1, 2, 3, 4, 5, 6, 7], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]), ([22, 23], []))
assert_test(([23, 22, 21, 1, 2, 3, 4, 5, 6, 7], [24, 11, 12, 13, 14, 15, 16, 17, 18, 19]), ([], [24]))
assert_test(([23, 22, 1, 2, 3, 4, 5, 6, 7, 8], [21, 24, 11, 12, 13, 14, 15, 16, 17, 18, 19]), ([], [21]))