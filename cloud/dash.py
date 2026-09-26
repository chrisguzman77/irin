"""C3: GET /v1/dash/{name}?days=: nights, tir, profile, lows_heatmap, alarms,
near_misses, basal, sensor, step_watch, buddy, under_the_hood; each a thin
query over one aggregate (cloud/sql/002) returning JSON the chart draws
directly. Dashboards draw pictures and never decide (invariant 21)."""


def query(name: str, days: int) -> dict:
    raise NotImplementedError("C3: dashboards")
