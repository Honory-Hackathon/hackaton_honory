# heat-views (T13): static pages + data, served by nginx on Cloud Run.
# Build context = views/site prepared by cloudbuild.yaml (T12 layers copied into site/data/t12, route heat built by build_data.py).
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/
