"""Tolesh test package.

Makes ``tests.*`` importable (e.g. ``tests.performance.timing_recorder``)
when pytest collects from the repository root. The upstream JARVIS source
omitted this file, which broke collection of the performance suite.
"""
