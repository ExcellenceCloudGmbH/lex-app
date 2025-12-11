import functools


def flatten(lists):
    return functools.reduce(lambda list1, list2: list1 + list2, lists, [])
