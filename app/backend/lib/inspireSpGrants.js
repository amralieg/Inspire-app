/**
 * Resolve the Inspire Databricks App service principal application ID for UC / warehouse grants.
 */

const DEFAULT_APP_NAME = 'inspire-ai';

async function resolveInspireAppServicePrincipalId(dbFetch, host, token, explicitId) {
  const fromEnv = String(
    explicitId ||
      process.env.DATABRICKS_CLIENT_ID ||
      process.env.SP_CLIENT_ID ||
      '',
  ).trim();
  if (fromEnv) return fromEnv;

  try {
    const filter = encodeURIComponent(`displayName co "${DEFAULT_APP_NAME}"`);
    const resp = await dbFetch(
      host,
      token,
      `/api/2.0/preview/scim/v2/ServicePrincipals?filter=${filter}&count=50`,
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    const resources = data.Resources || data.resources || [];
    for (const sp of resources) {
      const appId = String(sp.applicationId || sp.application_id || '').trim();
      if (appId) return appId;
    }
  } catch (e) {
    console.warn('Could not resolve inspire-ai service principal:', e.message || e);
  }
  return null;
}

module.exports = {
  resolveInspireAppServicePrincipalId,
  DEFAULT_APP_NAME,
};
