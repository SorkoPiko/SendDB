previous_levels = []
previous_rated_levels = []
rate_cache = {}

RATE_CACHE_TIME = 20

def test_send_results(levels: list[int], rated_levels: list[int], current_time: float) -> tuple[list[int], list[int]]:
	global previous_levels, previous_rated_levels, rate_cache

	filtered_levels = [level for level in levels if level not in rated_levels]

	if not previous_levels:
		previous_levels = levels.copy()
		previous_rated_levels = rated_levels.copy()
		return filtered_levels.copy(), rated_levels.copy()

	rates = [level for level in rated_levels if level not in previous_rated_levels]
	for level in rates:
		pending_rates[level] = current_time

	expired_levels = [level for level, timestamp in pending_rates.items() if current_time - timestamp > RATE_CACHE_TIME]
	for level in expired_levels:
		del pending_rates[level]

	prev_levels_working = previous_levels.copy()
	ignore_count = 0

	for level in pending_rates.keys():
		if level in prev_levels_working:
			prev_levels_working.remove(level)
			ignore_count += 1

	check_limit = len(filtered_levels) - ignore_count
	max_bumps = 0

	for i in range(check_limit):
		level = filtered_levels[i]
		prev_index = prev_levels_working.index(level) if level in prev_levels_working else float('inf')

		if i < prev_index:
			bumps_after = i
			total_bumps_including_this = bumps_after + 1
			max_bumps = max(max_bumps, total_bumps_including_this)

	sends = filtered_levels[:max_bumps]
	sends.reverse()

	previous_levels = levels.copy()
	previous_rated_levels = rated_levels.copy()

	return sends, rates

def assert_test(input, expected, timestamp):
	result = test_send_results(input[0], input[1], timestamp)
	assert result == expected, f"Expected {expected}, got {result}"

assert_test(([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]), ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]), 0)
assert_test(([21, 1, 2, 3, 4, 5, 6, 7, 8, 9], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]), ([21], []), 10)
assert_test(([23, 22, 21, 1, 2, 3, 4, 5, 6, 7], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]), ([22, 23], []), 20)
assert_test(([23, 22, 21, 1, 2, 3, 4, 5, 6, 7], [24, 11, 12, 13, 14, 15, 16, 17, 18, 19]), ([], [24]), 30)
assert_test(([23, 22, 1, 2, 3, 4, 5, 6, 7, 8], [21, 24, 11, 12, 13, 14, 15, 16, 17, 18, 19]), ([], [21]), 40)
assert_test(([23, 22, 2, 1, 3, 4, 5, 6, 7, 8], [21, 24, 11, 12, 13, 14, 15, 16, 17, 18, 19]), ([2, 22, 23], []), 50)
assert_test(([23, 22, 2, 1, 3, 4, 5, 6, 7, 8], [25, 21, 24, 11, 12, 13, 14, 15, 16, 17, 18]), ([], [25]), 60)

test_send_results([131264235, 130554388, 131463669, 131460992, 130554006, 116224870, 130564231, 127916601, 82523921, 129426868], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20], 0)
assert_test(([131264235, 130554388, 131463669, 131460992, 130554006, 116224870, 130564231, 127916601, 82523921, 129426868], [131264235, 11, 12, 13, 14, 15, 16, 17, 18, 19]), ([], [131264235]), 5)
assert_test(([130554388, 131463669, 131460992, 130554006, 116224870, 130564231, 127916601, 82523921, 129426868, 127517484], [131264235, 11, 12, 13, 14, 15, 16, 17, 18, 19]), ([], []), 10)
assert_test(([130554388, 131460992, 130554006, 116224870, 130564231, 127916601, 82523921, 129426868, 127517484, 129450237], [131463669, 131264235, 11, 12, 13, 14, 15, 16, 17, 18]), ([], [131463669]), 20)
assert_test(([130554388, 130554006, 116224870, 130564231, 127916601, 82523921, 129426868, 127517484, 129450237, 121664627], [131460992, 131463669, 131264235, 11, 12, 13, 14, 15, 16, 17]), ([], [131460992]), 30)
assert_test(([130554388, 130554006, 116224870, 127916601, 82523921, 129426868, 127517484, 129450237, 121664627, 123098463], [130564231, 131460992, 131463669, 131264235, 11, 12, 13, 14, 15, 16]), ([], [130564231]), 40)