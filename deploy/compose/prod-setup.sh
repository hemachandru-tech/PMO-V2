#!/usr/bin/env bash
# Render the production Keycloak realm (Entra ID as the only way in; no dev users) and the web config.
# Usage: ENTRA_TENANT_ID=… ENTRA_CLIENT_ID=… ENTRA_CLIENT_SECRET=… ./deploy/compose/prod-setup.sh
# Run ./deploy/compose/dev-setup.sh first (it makes the other secrets). Output is git-ignored (secrets/).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
export TELA_WEB_ORIGIN="${TELA_WEB_ORIGIN:-https://telainsight.prodiantela.ai}"
: "${ENTRA_TENANT_ID:?}" "${ENTRA_CLIENT_ID:?}" "${ENTRA_CLIENT_SECRET:?}"
python3 - "$here/../keycloak/realm-tela-prod.template.json" "$here/secrets/realm-tela-prod.json" <<'PY'
import os, re, sys
text = open(sys.argv[1]).read()
text = re.sub(r"\$\{([A-Z_]+)\}", lambda m: os.environ[m.group(1)], text)
open(sys.argv[2], "w").write(text)
PY
chmod 644 "$here/secrets/realm-tela-prod.json"
echo "Rendered secrets/realm-tela-prod.json for $TELA_WEB_ORIGIN"
