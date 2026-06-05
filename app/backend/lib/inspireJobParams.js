/** Notebook widget keys for dbx_inspire_ai_agent job base_parameters (v0.9+). */
const INSPIRE_NOTEBOOK_WIDGET_KEYS = [
  '15_operation',
  '00_business_name',
  '01_uc_metadata',
  '02_inspire_database',
  '04_table_election',
  '05_use_cases_quality',
  '06_business_domains',
  '07_business_priorities',
  '08_generation_instructions',
  '09_generation_options',
  '11_generation_path',
  '12_documents_languages',
  '13_generate_genie_code_for',
  '14_session_id',
];

function normalizeUseCasesQualityParams(params) {
  const uq = String(params['05_use_cases_quality'] || '').trim();
  const map = {
    Balanced: 'High Quality',
    'Strict Quality': 'Very High Quality',
    'Coverage Mode (All)': 'Good Quality',
    'High Quality': 'High Quality',
    'Very High Quality': 'Very High Quality',
    'Good Quality': 'Good Quality',
  };
  if (map[uq]) params['05_use_cases_quality'] = map[uq];
  if (!params['05_use_cases_quality']) params['05_use_cases_quality'] = 'Balanced';
  return params;
}

function sanitizeInspireNotebookJobParams(params) {
  const out = {};
  for (const k of INSPIRE_NOTEBOOK_WIDGET_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(params, k)) continue;
    const v = params[k];
    if (v == null) continue;
    out[k] = String(v);
  }
  if (!out['13_generate_genie_code_for']) out['13_generate_genie_code_for'] = '5';
  return out;
}

/** Static + task-value parameters for demo pipeline task 2 (Inspire discovery). */
function buildDemoInspireDiscoveryParams({ inspireDatabase, sessionId, generateTaskKey = 'generate_demo_data' }) {
  const tk = generateTaskKey;
  const raw = {
    '15_operation': 'Discover Use Cases',
    '00_business_name': `{{tasks.${tk}.values.display_name}}`,
    '01_uc_metadata': `{{tasks.${tk}.values.uc_metadata}}`,
    '02_inspire_database': inspireDatabase,
    '04_table_election': 'All Tables',
    '05_use_cases_quality': 'Balanced',
    '06_business_domains': '',
    '07_business_priorities': 'Increase Revenue',
    '08_generation_instructions': `{{tasks.${tk}.values.user_description}}`,
    '09_generation_options': 'PDF Catalog,Genie Code Instructions',
    '11_generation_path': './../demos/',
    '12_documents_languages': 'English',
    '13_generate_genie_code_for': '5',
    '14_session_id': sessionId,
  };
  normalizeUseCasesQualityParams(raw);
  return sanitizeInspireNotebookJobParams(raw);
}

module.exports = {
  buildDemoInspireDiscoveryParams,
  sanitizeInspireNotebookJobParams,
  normalizeUseCasesQualityParams,
};
