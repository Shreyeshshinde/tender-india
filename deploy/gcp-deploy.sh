#!/usr/bin/env bash
# Deploy Tender India to Google Cloud (free tier) with Supabase as the database.
#
#   tender-web      Cloud Run service   the website (Next.js), password protected
#   tender-api      Cloud Run service   the API (FastAPI)
#   tender-scraper  Cloud Run Job       one refresh = every scraper once (Playwright)
#   Cloud Scheduler                     optional automatic refresh (AUTO_REFRESH_CRON)
#
# Run from the project root in Git Bash, after `gcloud auth login`:
#   cp deploy/gcp.env.example deploy/gcp.env    # then fill it in
#   bash deploy/gcp-deploy.sh
# Safe to run again: it updates what exists and creates what is missing.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f deploy/gcp.env ] || { echo "Missing deploy/gcp.env (copy deploy/gcp.env.example)"; exit 1; }
set -a; . deploy/gcp.env; set +a

: "${GCP_PROJECT:?Set GCP_PROJECT in deploy/gcp.env}"
: "${DATABASE_URL:?Set DATABASE_URL (Supabase session pooler) in deploy/gcp.env}"
if [ -z "${SITE_PASSWORD:-}" ] && [ "${ALLOW_PUBLIC_SITE:-}" != "1" ]; then
  echo "Set SITE_PASSWORD in deploy/gcp.env (or ALLOW_PUBLIC_SITE=1 to let anyone delete/refresh)."
  exit 1
fi
REGION="${GCP_REGION:-asia-south1}"
SITE_USER="${SITE_USER:-admin}"
REPO=tender
REG="$REGION-docker.pkg.dev/$GCP_PROJECT/$REPO"
TAG="$(date +%Y%m%d-%H%M%S)"
API_SA="tender-api@$GCP_PROJECT.iam.gserviceaccount.com"
JOB_SA="tender-scraper@$GCP_PROJECT.iam.gserviceaccount.com"
WEB_SA="tender-web@$GCP_PROJECT.iam.gserviceaccount.com"

step() { printf '\n==> %s\n' "$*"; }
retry() { local n=0; until "$@"; do n=$((n + 1)); [ $n -ge 6 ] && return 1; echo "   (retrying in 10s)"; sleep 10; done; }

gcloud config set project "$GCP_PROJECT" >/dev/null

step "Checking billing is linked (needed even for the free tier)"
gcloud services enable cloudbilling.googleapis.com
if [ "$(gcloud billing projects describe "$GCP_PROJECT" --format='value(billingEnabled)')" != "True" ]; then
  echo "Billing is not enabled for $GCP_PROJECT. Link a billing account in the Console (Billing), then run again."
  exit 1
fi

step "Turning on the services we use"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  cloudscheduler.googleapis.com iam.googleapis.com billingbudgets.googleapis.com

step "Image storage ($REG)"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location "$REGION" \
    --description="Tender India images"
gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" \
  --policy=deploy/ar-cleanup-policy.json --no-dry-run >/dev/null
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet >/dev/null

step "Service accounts (one per part, each with only what it needs)"
for sa in tender-api tender-scraper tender-web; do
  gcloud iam service-accounts describe "$sa@$GCP_PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$sa" --display-name "Tender India $sa"
done

step "Secrets (Secret Manager)"
put_secret() {   # name value
  local name=$1 value=$2
  if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets create "$name" --replication-policy=user-managed \
      --locations="$REGION" --data-file=- >/dev/null
  elif [ "$(gcloud secrets versions access latest --secret="$name")" != "$value" ]; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    # keep only the newest version (free tier: 6 active versions in total)
    for v in $(gcloud secrets versions list "$name" --filter='state=ENABLED' --sort-by=~createTime \
               --format='value(name)' | tail -n +2); do
      gcloud secrets versions destroy "$v" --secret="$name" --quiet >/dev/null
    done
  fi
}
grant_secret() {  # secret service-account
  retry gcloud secrets add-iam-policy-binding "$1" --member "serviceAccount:$2" \
    --role roles/secretmanager.secretAccessor >/dev/null
}
if [ -z "${ADMIN_TOKEN:-}" ]; then
  ADMIN_TOKEN="$(gcloud secrets versions access latest --secret=tender-admin-token 2>/dev/null || openssl rand -hex 24)"
fi
put_secret tender-database-url "$DATABASE_URL"
put_secret tender-admin-token "$ADMIN_TOKEN"
grant_secret tender-database-url "$API_SA"
grant_secret tender-database-url "$JOB_SA"
grant_secret tender-admin-token "$API_SA"
grant_secret tender-admin-token "$WEB_SA"
if [ -n "${SITE_PASSWORD:-}" ]; then
  put_secret tender-site-password "$SITE_PASSWORD"
  grant_secret tender-site-password "$WEB_SA"
fi

step "Building and uploading the backend image (API + scrapers)"
docker build -t "$REG/backend:$TAG" backend
docker push "$REG/backend:$TAG"

step "Scraper job (runs only when you press Refresh or on the schedule)"
gcloud run jobs deploy tender-scraper --image "$REG/backend:$TAG" --region "$REGION" \
  --command python --args="-m,app.schedule,--once" \
  --tasks 1 --max-retries 0 --task-timeout "${SCRAPER_TIMEOUT:-6h}" --cpu 1 --memory 2Gi \
  --service-account "$JOB_SA" \
  --set-secrets DATABASE_URL=tender-database-url:latest \
  --set-env-vars "DB_POOL_SIZE=2,DB_MAX_OVERFLOW=2,REFRESH_STALE_HOURS=7"

step "API service"
gcloud run deploy tender-api --image "$REG/backend:$TAG" --region "$REGION" \
  --service-account "$API_SA" --allow-unauthenticated \
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 2 --concurrency 40 --timeout 60 \
  --set-secrets DATABASE_URL=tender-database-url:latest,ADMIN_TOKEN=tender-admin-token:latest \
  --set-env-vars "REFRESH_RUNNER=cloudrun,GCP_PROJECT=$GCP_PROJECT,GCP_REGION=$REGION,SCRAPER_JOB=tender-scraper,DOCUMENTS_ENABLED=0,DB_POOL_SIZE=2,DB_MAX_OVERFLOW=3,REFRESH_STALE_HOURS=7"
# the API may start the scraper job (Refresh button)
retry gcloud run jobs add-iam-policy-binding tender-scraper --region "$REGION" \
  --member "serviceAccount:$API_SA" --role roles/run.invoker >/dev/null
API_URL="$(gcloud run services describe tender-api --region "$REGION" --format='value(status.url)')"

step "Building and uploading the website image (API: $API_URL)"
docker build --build-arg API_URL="$API_URL" -t "$REG/web:$TAG" frontend
docker push "$REG/web:$TAG"

step "Website service"
WEB_SECRETS="ADMIN_TOKEN=tender-admin-token:latest"
[ -n "${SITE_PASSWORD:-}" ] && WEB_SECRETS="$WEB_SECRETS,SITE_PASSWORD=tender-site-password:latest"
gcloud run deploy tender-web --image "$REG/web:$TAG" --region "$REGION" \
  --service-account "$WEB_SA" --allow-unauthenticated \
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 2 --concurrency 40 --timeout 60 \
  --set-env-vars "API_URL=$API_URL,SITE_USER=$SITE_USER" \
  --set-secrets "$WEB_SECRETS"
WEB_URL="$(gcloud run services describe tender-web --region "$REGION" --format='value(status.url)')"

step "Automatic refresh"
JOB_RUN_URI="https://run.googleapis.com/v2/projects/$GCP_PROJECT/locations/$REGION/jobs/tender-scraper:run"
if [ -n "${AUTO_REFRESH_CRON:-}" ]; then
  if gcloud scheduler jobs describe tender-auto-refresh --location "$REGION" >/dev/null 2>&1; then
    verb=update
  else
    verb=create
  fi
  gcloud scheduler jobs "$verb" http tender-auto-refresh --location "$REGION" \
    --schedule "$AUTO_REFRESH_CRON" --time-zone "Asia/Kolkata" --uri "$JOB_RUN_URI" \
    --http-method POST --oauth-service-account-email "$API_SA" >/dev/null
  echo "Scheduled: $AUTO_REFRESH_CRON (India time)"
else
  gcloud scheduler jobs delete tender-auto-refresh --location "$REGION" --quiet >/dev/null 2>&1 || true
  echo "Off (Refresh button only)"
fi

step "Spending alert (emails you if anything ever costs money)"
BILLING="$(gcloud billing projects describe "$GCP_PROJECT" --format='value(billingAccountName)')"
BILLING="${BILLING#billingAccounts/}"
if gcloud billing budgets list --billing-account "$BILLING" --format='value(displayName)' 2>/dev/null |
     grep -qx "Tender India alert"; then
  echo "Already set"
else
  CUR="$(gcloud billing accounts describe "$BILLING" --format='value(currencyCode)' 2>/dev/null || echo USD)"
  AMOUNT=1; [ "$CUR" = "INR" ] && AMOUNT=100
  gcloud billing budgets create --billing-account "$BILLING" --display-name "Tender India alert" \
    --budget-amount "${AMOUNT}${CUR}" --filter-projects "projects/$GCP_PROJECT" \
    --threshold-rule percent=0.01 --threshold-rule percent=0.5 --threshold-rule percent=1.0 >/dev/null &&
    echo "Alert at $AMOUNT $CUR" ||
    echo "Could not create the budget alert (needs Billing Account Administrator). Add one in Console > Billing > Budgets."
fi

printf '\nDone.\n  Website: %s  (user: %s)\n  API:     %s\n' "$WEB_URL" "$SITE_USER" "$API_URL"
