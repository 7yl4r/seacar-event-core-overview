.PHONY: build serve deploy

# regenerate the overview site from the DwC archives
build:
	python3 build_overview.py

# local preview (http://localhost:8000) using the static data files
serve:
	cd output && python3 -m http.server 8000

# commit everything, push main, and publish output/ to the gh-pages branch
deploy:
	bash deploy.sh
