#!/usr/bin/env node
/**
 * Verify local Inspire AI can reach your Databricks workspace.
 * Run from repo root: node scripts/verify-local-databricks.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

function loadEnv() {
  const envPath = path.join(root, '.env');
  if (!fs.existsSync(envPath)) {
    console.error('❌ Missing .env — run: cp .env.example .env');
    process.exit(1);
  }
  const env = {};
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const eq = t.indexOf('=');
    if (eq === -1) continue;
    const key = t.slice(0, eq).trim();
    let val = t.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    env[key] = val;
  }
  return env;
}

async function main() {
  const env = loadEnv();
  const host = (env.DATABRICKS_HOST || '').replace(/\/$/, '');
  const token = env.DATABRICKS_TOKEN || '';
  const warehouseId = env.INSPIRE_WAREHOUSE_ID || '';
  const inspireDb = env.INSPIRE_DATABASE || '';

  console.log('\n══ Inspire AI — local Databricks connection check ══\n');

  if (!host) {
    console.error('❌ DATABRICKS_HOST is not set in .env');
    process.exit(1);
  }
  console.log(`Host:      ${host}`);
  console.log(`Token:     ${token ? `${token.slice(0, 8)}… (${token.length} chars)` : 'NOT SET'}`);
  console.log(`Warehouse: ${warehouseId || 'NOT SET'}`);
  console.log(`Inspire DB: ${inspireDb || 'NOT SET'}`);
  console.log('');

  if (!token) {
    console.error('❌ DATABRICKS_TOKEN missing. Create a PAT in Databricks → User Settings → Developer → Access tokens');
    process.exit(1);
  }

  const headers = { Authorization: `Bearer ${token}` };

  // 1) Workspace reachability
  let whResp;
  try {
    whResp = await fetch(`${host}/api/2.0/sql/warehouses`, { headers });
  } catch (e) {
    console.error(`❌ Cannot reach workspace: ${e.message}`);
    console.error('   Check VPN, firewall, and that DATABRICKS_HOST has no trailing path or ?o= query.');
    process.exit(1);
  }
  const whJson = await whResp.json();
  if (!whResp.ok) {
    console.error(`❌ Auth failed (${whResp.status}): ${whJson.message || JSON.stringify(whJson)}`);
    console.error('   Regenerate your PAT and update DATABRICKS_TOKEN in .env');
    process.exit(1);
  }
  const warehouses = whJson.warehouses || [];
  console.log(`✅ Workspace reachable — ${warehouses.length} SQL warehouse(s)`);

  if (warehouseId) {
    const found = warehouses.find((w) => w.id === warehouseId);
    console.log(found ? `✅ INSPIRE_WAREHOUSE_ID valid (${found.name})` : `⚠️  INSPIRE_WAREHOUSE_ID not in list — pick one from SQL Warehouses UI`);
  } else {
    const pick = warehouses.find((w) => w.state === 'RUNNING') || warehouses[0];
    if (pick) console.log(`ℹ️  Set INSPIRE_WAREHOUSE_ID=${pick.id}  # ${pick.name}`);
  }

  // 2) Catalogs (needs warehouse for SQL fallback)
  const wh = warehouseId || warehouses.find((w) => w.state === 'RUNNING')?.id || warehouses[0]?.id;
  if (wh) {
    const catResp = await fetch(`${host}/api/2.0/sql/statements`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        warehouse_id: wh,
        statement: 'SHOW CATALOGS',
        wait_timeout: '30s',
        disposition: 'INLINE',
        format: 'JSON_ARRAY',
      }),
    });
    const catJson = await catResp.json();
    if (catResp.ok && catJson.status?.state === 'SUCCEEDED') {
      const rows = catJson.result?.data_array?.length ?? 0;
      console.log(`✅ Unity Catalog browse OK (${rows} catalog row(s) via warehouse)`);
    } else {
      console.log(`⚠️  SHOW CATALOGS: ${catJson.status?.error?.message || catResp.status}`);
    }
  }

  // 3) Inspire schema
  if (inspireDb && wh) {
    const [catalog, schema] = inspireDb.split('.');
    if (catalog && schema) {
      const sql = `CREATE SCHEMA IF NOT EXISTS \`${catalog}\`.\`${schema}\``;
      const schResp = await fetch(`${host}/api/2.0/sql/statements`, {
        method: 'POST',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          warehouse_id: wh,
          statement: sql,
          wait_timeout: '30s',
        }),
      });
      const schJson = await schResp.json();
      if (schResp.ok && (schJson.status?.state === 'SUCCEEDED' || schJson.status?.state === 'PENDING')) {
        console.log(`✅ Inspire tracking schema ensured: ${inspireDb}`);
      } else {
        console.log(`⚠️  Could not create schema ${inspireDb}: ${schJson.status?.error?.message || schResp.status}`);
      }
    }
  } else {
    console.log('ℹ️  Set INSPIRE_DATABASE=catalog._inspire (catalog + _inspire schema for session tables)');
  }

  console.log('\n── Next steps ──');
  console.log('  npm run dev');
  console.log('  Open http://localhost:5173/');
  console.log('  If the Setup Wizard appears, complete it or fill all four vars in .env to skip it.\n');
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
