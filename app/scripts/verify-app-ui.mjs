#!/usr/bin/env node
/**
 * Verify the deployed Inspire AI app includes the "No data yet" UI and demo-data API.
 *
 * Local bundle only:
 *   node scripts/verify-app-ui.mjs
 *
 * Live Databricks App (open app while logged in, copy URL without path):
 *   INSPIRE_APP_URL="https://<your-app-url>" node scripts/verify-app-ui.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const distJs = path.join(root, 'frontend/dist/assets');

function checkLocalBundle() {
  if (!fs.existsSync(distJs)) {
    return { ok: false, message: 'frontend/dist missing — run: cd frontend && npm run build' };
  }
  const jsFiles = fs.readdirSync(distJs).filter((f) => f.startsWith('index-') && f.endsWith('.js'));
  if (!jsFiles.length) {
    return { ok: false, message: 'no index-*.js in frontend/dist/assets' };
  }
  const latest = jsFiles.sort().pop();
  const text = fs.readFileSync(path.join(distJs, latest), 'utf8');
  const hasUi = text.includes('No data yet');
  return {
    ok: hasUi,
    jsBundle: latest,
    hasDemoDataUi: hasUi,
    message: hasUi
      ? `Local bundle OK (${latest})`
      : `Local bundle STALE (${latest}) — missing "No data yet"; rebuild and redeploy`,
  };
}

async function checkLiveApp(baseUrl) {
  const url = baseUrl.replace(/\/$/, '');
  const defaultsResp = await fetch(`${url}/api/defaults`);
  if (!defaultsResp.ok) {
    return { ok: false, message: `/api/defaults returned ${defaultsResp.status}` };
  }
  const defaults = await defaultsResp.json();
  const templatesResp = await fetch(`${url}/api/demo-data/templates`);
  const templatesOk = templatesResp.ok;

  const ui = defaults?.frontendBuild?.hasDemoDataUi ?? defaults?.features?.demoDataUi;
  const ok = ui === true && templatesOk;
  return {
    ok,
    jsBundle: defaults?.frontendBuild?.jsBundle,
    hasDemoDataUi: ui,
    demoDataApi: templatesOk,
    message: ok
      ? `App OK — UI bundle ${defaults?.frontendBuild?.jsBundle || '?'}`
      : [
          ui ? null : 'UI bundle missing "No data yet" (stale deploy — run npm run deploy)',
          templatesOk ? null : '/api/demo-data/templates not found (stale backend)',
        ]
          .filter(Boolean)
          .join('; ') || 'check failed',
  };
}

async function main() {
  console.log('\n══ Inspire AI — "No data yet" UI check ══\n');

  const local = checkLocalBundle();
  console.log('Local zip source:');
  console.log(`  ${local.ok ? '✅' : '❌'} ${local.message}`);
  if (local.jsBundle) console.log(`  bundle: ${local.jsBundle}`);

  const appUrl = process.env.INSPIRE_APP_URL || process.env.DATABRICKS_APP_URL || '';
  if (!appUrl) {
    console.log('\nLive app: skipped (set INSPIRE_APP_URL to your inspire-ai app URL)\n');
    process.exit(local.ok ? 0 : 1);
  }

  console.log(`\nLive app (${appUrl}):`);
  try {
    const live = await checkLiveApp(appUrl);
    console.log(`  ${live.ok ? '✅' : '❌'} ${live.message}`);
    if (live.jsBundle) console.log(`  bundle: ${live.jsBundle}`);
    console.log('');
    process.exit(local.ok && live.ok ? 0 : 1);
  } catch (e) {
    console.log(`  ❌ ${e.message}`);
    console.log('  Open the app in your browser while logged in, then retry.\n');
    process.exit(1);
  }
}

main();
