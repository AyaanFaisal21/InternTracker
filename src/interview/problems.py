"""The built-in problem set.

Three problems is a scaffold, not a product. The real set moves to a YAML
file next to config/watchlist.yaml so problems can be added without a
deploy; the shape below is what that file has to produce.

`pressure_points` are written as interviewer questions on purpose. The live
agent reads them as suggestions and phrases its own; the stub interviewer
asks them verbatim, which is how the offline path stays useful without a
model.
"""

from __future__ import annotations

from .schema import Problem

PROBLEMS: dict[str, Problem] = {
    p.id: p
    for p in (
        Problem(
            id="two-sum",
            title="Two Sum",
            difficulty="easy",
            statement=(
                "Given an array of integers and a target, return the indices of "
                "the two numbers that add up to the target. Exactly one pair "
                "exists."
            ),
            constraints=[
                "2 <= len(nums) <= 10^5",
                "-10^9 <= nums[i] <= 10^9",
                "values may repeat",
            ],
            examples=[
                "nums = [2, 7, 11, 15], target = 9 -> [0, 1]",
                "nums = [3, 3], target = 6 -> [0, 1]",
            ],
            optimal="O(n) time, O(n) space, one pass with a value to index map",
            pressure_points=[
                "You said a hash map. What property of a hash map does this "
                "problem actually need?",
                "Walk me through what your map holds when the array is "
                "[3, 3] and the target is 6.",
                "You said O(n). Which operation are you assuming is constant "
                "time, and when is that assumption wrong?",
                "The values go up to ten to the ninth. Does anything in your "
                "approach care about the size of the values?",
                "If the array were already sorted, would you still write this "
                "solution? Why?",
            ],
        ),
        Problem(
            id="merge-intervals",
            title="Merge Intervals",
            difficulty="medium",
            statement=(
                "Given a list of intervals, merge all overlapping intervals "
                "and return the result in any order."
            ),
            constraints=[
                "1 <= len(intervals) <= 10^4",
                "each interval is [start, end] with start <= end",
                "the input is not sorted",
            ],
            examples=[
                "[[1,3],[2,6],[8,10],[15,18]] -> [[1,6],[8,10],[15,18]]",
                "[[1,4],[4,5]] -> [[1,5]]",
            ],
            optimal="O(n log n) time from the sort, O(n) space for the output",
            pressure_points=[
                "You sorted first. What breaks if you do not?",
                "What does your code return for [[1,4],[4,5]], and is touching "
                "at a single point an overlap or not?",
                "You said O(n). The sort is in your solution. Which step "
                "dominates?",
                "What happens on a single interval, and on ten thousand "
                "intervals that all overlap?",
                "Suppose the intervals arrive one at a time instead of as a "
                "list. Does your approach still work?",
            ],
        ),
        Problem(
            id="lru-cache",
            title="LRU Cache",
            difficulty="hard",
            statement=(
                "Design a cache with a fixed capacity that supports get and "
                "put in constant time. When the cache is full, evict the least "
                "recently used entry."
            ),
            constraints=[
                "1 <= capacity <= 10^4",
                "get and put must both be O(1) average",
                "a get counts as a use",
            ],
            examples=[
                "capacity 2; put(1,1) put(2,2) get(1)=1 put(3,3) get(2)=-1",
            ],
            optimal="hash map to node, plus a doubly linked list for recency",
            pressure_points=[
                "You need constant time for both operations. Which single data "
                "structure fails that, and on which operation?",
                "Why a doubly linked list and not a singly linked one?",
                "A get on an existing key: what exactly changes in your "
                "structure, step by step?",
                "What happens when the capacity is one and you put the same "
                "key twice?",
                "Two threads call put at the same time. What breaks?",
            ],
        ),
    )
}


def get_problem(problem_id: str) -> Problem | None:
    """The problem, or None. Callers treat None as an unknown id from a
    client, not as an error worth raising on."""
    return PROBLEMS.get(problem_id)
