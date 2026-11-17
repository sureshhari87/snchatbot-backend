# Isolated recovery validation

This is a test-only source snapshot. Do not merge this temporary validation branch
into production or deploy the snapshot folder as a replacement application.

The parent GitHub branch contains the newer application. The validation workflow
deliberately does NOT build that application. It builds only `source/`, copied
from live Hugging Face commit `23f1352ebcecdf21df18ab03b5aa5363862be045`, plus
the reviewed three-file recovery patch. The 123 source files are recorded in
`SOURCE-MANIFEST.json` and checked before and inside the container.

The test-only branch uses an existing GitHub commit as its parent because the
Hugging Face commit is not present in GitHub history. Provenance identifies both
commits. No GitHub production branch or Hugging Face ref is updated.

The workflow has read-only repository permissions, no environment or secret
references, and no deployment steps. Build-time package downloads are needed.
Runtime tests use network-isolated Linux containers, synthetic credentials,
temporary SQLite fixtures, no operator keys, and no migrations. The normal
startup check creates a NEW fixture schema inside temporary container storage;
it is not a database migration or contact with Neon/production.

The existing broad CI workflow is skipped only for this temporary test branch,
so its unrelated tests and deployment jobs cannot be triggered by this push.
The regular application working checkout and its uncommitted files are retained.

A successful run is Linux build/test evidence, NOT an actual Hugging Face
deployment, reverse-proxy test or live recovery. Any production deployment,
restart or live export still requires separate explicit approval.
