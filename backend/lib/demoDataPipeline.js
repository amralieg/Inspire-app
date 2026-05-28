const fs = require('fs');
const path = require('path');
const { buildDemoSchemaName } = require('./demoDataService');
const { buildDemoInspireDiscoveryParams } = require('./inspireJobParams');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const GENERATE_NOTEBOOK_DEST = '/Shared/inspire-ai/dbx_generate_demo_data';
const GENERATE_NOTEBOOK_LOCAL = path.join(REPO_ROOT, 'dbx_generate_demo_data.py');

async function publishPipelineNotebook(dbFetch, host, token, localPath, destPath) {
  if (!fs.existsSync(localPath)) {
    throw new Error(`Pipeline notebook not found: ${localPath}`);
  }
  const content = fs.readFileSync(localPath).toString('base64');
  try {
    await dbFetch(host, token, '/api/2.0/workspace/mkdirs', {
      method: 'POST',
      body: JSON.stringify({ path: path.posix.dirname(destPath) }),
    });
  } catch (_) {
    /* ignore */
  }
  const resp = await dbFetch(host, token, '/api/2.0/workspace/import', {
    method: 'POST',
    body: JSON.stringify({
      path: destPath,
      format: 'SOURCE',
      content,
      language: 'PYTHON',
      overwrite: true,
    }),
  });
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Publish ${destPath} failed: ${err}`);
  }
  return destPath;
}

async function resolveInspireNotebookPath(host, token, { ensureInspireNotebookPublished, resolveWorkspaceNotebookObjectPath }) {
  let path = '/Shared/inspire_ai';
  if (ensureInspireNotebookPublished) {
    const pub = await ensureInspireNotebookPublished(host, token);
    if (pub?.path) path = pub.path;
  }
  if (resolveWorkspaceNotebookObjectPath) {
    const verified = await resolveWorkspaceNotebookObjectPath(host, token, path);
    if (verified) return verified;
  }
  throw new Error(
    `Inspire agent notebook not found at ${path}. Open Workspace setup or run a normal Inspire job once to auto-publish.`,
  );
}

async function resolvePipelineClusterId(dbFetch, host, token, clusterId) {
  const explicit = String(clusterId || '').trim();
  if (explicit) return explicit;
  const fromEnv = String(
    process.env.INSPIRE_CLUSTER_ID || process.env.INSPIRE_DEPLOY_CLUSTER_ID || '',
  ).trim();
  if (fromEnv) return fromEnv;
  try {
    const resp = await dbFetch(host, token, '/api/2.0/clusters/list');
    if (resp.ok) {
      const { clusters = [] } = await resp.json();
      const running = clusters.find((c) => c.state === 'RUNNING');
      if (running?.cluster_id) {
        console.log(`📋 Demo pipeline: using running cluster ${running.cluster_id}`);
        return running.cluster_id;
      }
    }
  } catch (e) {
    console.warn('Could not list clusters for demo pipeline:', e.message || e);
  }
  return null;
}

/**
 * Two-task job: generate demo UC tables → run Inspire agent directly (no nested notebook.run).
 */
async function triggerDemoDataPipeline(
  dbFetch,
  databricksJobRunUrl,
  host,
  token,
  {
    description,
    businessName,
    inspireDatabase,
    sessionId,
    cluster_id,
    warehouse_id,
    app_sp_application_id,
    ensureInspireNotebookPublished,
    resolveWorkspaceNotebookObjectPath,
  },
) {
  const desc = String(description || '').trim();
  const business = String(businessName || '').trim();
  const inspireDb = String(inspireDatabase || '').trim();
  const sid =
    String(sessionId || '').trim() || `${Date.now()}${Math.floor(Math.random() * 1e6)}`;

  if (!desc) throw new Error('description is required');
  if (!business) throw new Error('business_name is required');
  if (!inspireDb || !inspireDb.includes('.')) {
    throw new Error('inspire_database must be catalog._inspire');
  }

  const [catalog] = inspireDb.split('.');
  const demoSchema = buildDemoSchemaName(business, sid);

  await publishPipelineNotebook(dbFetch, host, token, GENERATE_NOTEBOOK_LOCAL, GENERATE_NOTEBOOK_DEST);

  const inspireNotebookPath = await resolveInspireNotebookPath(host, token, {
    ensureInspireNotebookPublished,
    resolveWorkspaceNotebookObjectPath,
  });

  const resolvedClusterId = await resolvePipelineClusterId(dbFetch, host, token, cluster_id);
  const clusterSpec = resolvedClusterId
    ? { existing_cluster_id: resolvedClusterId }
    : { environment_key: 'Default' };

  const inspireParams = buildDemoInspireDiscoveryParams({
    inspireDatabase: inspireDb,
    sessionId: sid,
  });

  const createPayload = {
    name: `Inspire AI Demo Pipeline - ${business} - ${new Date().toISOString().slice(0, 19)}`,
    tags: {
      inspire_version: 'v0.9.0',
      dbx_inspire_ai_type: 'demo_pipeline',
      dbx_inspire_ai_session: String(sid).replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 255),
    },
    tasks: [
      {
        task_key: 'generate_demo_data',
        notebook_task: {
          notebook_path: GENERATE_NOTEBOOK_DEST,
          source: 'WORKSPACE',
          base_parameters: {
            user_description: desc,
            inspire_catalog: catalog,
            inspire_database: inspireDb,
            session_id: sid,
            business_name: business,
            demo_schema: demoSchema,
            warehouse_id: String(warehouse_id || '').trim(),
            app_sp_application_id: String(app_sp_application_id || '').trim(),
          },
        },
        ...clusterSpec,
      },
      {
        task_key: 'inspire_discovery',
        depends_on: [{ task_key: 'generate_demo_data' }],
        notebook_task: {
          notebook_path: inspireNotebookPath,
          source: 'WORKSPACE',
          base_parameters: inspireParams,
        },
        ...clusterSpec,
      },
    ],
    ...(!resolvedClusterId
      ? {
          environments: [{ environment_key: 'Default', spec: { client: '1' } }],
        }
      : {}),
    max_concurrent_runs: 1,
  };

  console.log(
    `📋 Demo pipeline: ${GENERATE_NOTEBOOK_DEST} → ${inspireNotebookPath} (cluster=${resolvedClusterId || 'serverless'}, session=${sid})`,
  );

  const createResp = await dbFetch(host, token, '/api/2.1/jobs/create', {
    method: 'POST',
    body: JSON.stringify(createPayload),
  });
  if (!createResp.ok) {
    const errText = await createResp.text();
    throw new Error(`Demo pipeline job create failed: ${errText}`);
  }
  const { job_id: jobId } = await createResp.json();

  const runResp = await dbFetch(host, token, '/api/2.1/jobs/run-now', {
    method: 'POST',
    body: JSON.stringify({ job_id: jobId }),
  });
  if (!runResp.ok) {
    const errText = await runResp.text();
    throw new Error(`Demo pipeline run-now failed: ${errText}`);
  }
  const { run_id: runId } = await runResp.json();

  return {
    session_id: sid,
    job_id: jobId,
    run_id: runId,
    job_run_url: databricksJobRunUrl(host, jobId, runId),
    pipeline: {
      step1_notebook: GENERATE_NOTEBOOK_DEST,
      step2_notebook: inspireNotebookPath,
      inspire_agent_notebook: inspireNotebookPath,
      inspire_database: inspireDb,
      demo_schema_planned: `${catalog}.${demoSchema}`,
      catalog,
      cluster_id: resolvedClusterId || null,
    },
  };
}

module.exports = {
  triggerDemoDataPipeline,
  publishPipelineNotebook,
};
