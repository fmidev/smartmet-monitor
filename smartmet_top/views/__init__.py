"""Composite views: full-screen layouts that draw multiple panels at once.

A View is a Panel subclass — same draw/handle_key/export interface — so
the App treats it identically to any single-panel entry in its panel
list. Internally a View holds a list of sub-panels and a geometry
function that lays them out as derwin'd sub-windows of the parent.

Views are still alpha-stage scaffolding; expect the layouts and the
groupings between them to evolve from operator feedback.
"""
