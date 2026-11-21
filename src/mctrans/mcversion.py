from __future__ import annotations

import re


def parse_version(version: str) -> tuple[int, int, int]:
    """把 "1.20.1"、"1.21"、"26.1-pre2" 之类的版本号解析成三元组，用于比较大小。"""
    nums = [int(n) for n in re.findall(r"\d+", version)[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]
