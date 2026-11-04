# SEACAR DwC Event-Core overview (dwc-viewer)

Simple static overview built from ../seacar2dwc/data/dwc/<id>/archive/event.txt files.
`build_overview.py` aggregates every dataset's Event-Core coordinates into H3
hexagons and per-year counts; the page is plain HTML/JS (Leaflet + ECharts + h3-js).

Published at: https://7yl4r.github.io/seacar-event-core-overview/

 Targets:
   make build   regenerate output/ from ../seacar2dwc/data/dwc (python3, h3, pandas)
   make serve   local preview of output/ on :8000
   make deploy  commit to main and publish output/ to gh-pages (no Actions needed)
